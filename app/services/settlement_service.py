"""테이블별 정산 / 일괄 마감 서비스 (매장 관리자).

  - 테이블별 요약: 진행 중 주문(ACTIVE_STATUSES)을 테이블 단위로 묶어 메뉴별 수량/금액 합계를 낸다.
  - 일괄 마감: 테이블의 진행 중 주문이 모두 서빙 완료(SERVED)일 때만 한꺼번에 COMPLETED로 바꾼다.
    화면에서 본 주문 id 목록을 함께 받아, 그 사이 새 주문이 들어왔으면 마감하지 않는다.
  - 오늘 매출: 영업 시간대(BUSINESS_TIMEZONE) 기준 오늘 마감된 주문 합계.
"""
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.events import notify_order_changed
from app.database.models import Order, OrderItem, OrderStatus, Table, TableStatus
from app.domain.order_status import ACTIVE_STATUSES, validate_transition


class SettlementError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class MenuLine:
    """같은 메뉴 + 같은 옵션 + 같은 단가는 한 줄로 합친다"""
    name: str
    options: tuple[str, ...]
    quantity: int = 0
    amount: int = 0


@dataclass
class TableSummary:
    table: Table
    orders: list[Order] = field(default_factory=list)  # 진행 중 주문 (오래된 순)
    lines: list[MenuLine] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(o.total_price for o in self.orders)

    @property
    def pending(self) -> list[Order]:
        """아직 서빙 완료가 아닌 주문 (있으면 마감 불가)"""
        return [o for o in self.orders if o.status is not OrderStatus.SERVED]

    @property
    def can_close(self) -> bool:
        return bool(self.orders) and not self.pending

    @property
    def order_ids(self) -> str:
        return ",".join(str(o.id) for o in self.orders)

    @property
    def started_at(self) -> datetime | None:
        return self.orders[0].created_at if self.orders else None


@dataclass
class TodaySummary:
    completed_count: int
    completed_amount: int
    cancelled_count: int


def _aggregate(orders: Iterable[Order]) -> list[MenuLine]:
    lines: dict[tuple, MenuLine] = {}
    for order in orders:
        for item in order.items:
            options = tuple(o.option_name for o in item.options)
            unit = item.subtotal_price // item.quantity if item.quantity else item.subtotal_price
            line = lines.setdefault((item.item_name, options, unit), MenuLine(item.item_name, options))
            line.quantity += item.quantity
            line.amount += item.subtotal_price
    return list(lines.values())


# =====================================================================
# 조회
# =====================================================================
async def get_table_summaries(db: AsyncSession, store_id: int) -> list[TableSummary]:
    """매장의 테이블별 정산 요약 (테이블 번호순).
    '사용 안 함' 테이블은 진행 중 주문이 남아 있을 때만 포함한다 (마감은 할 수 있어야 하므로)."""
    tables = (
        await db.execute(select(Table).where(Table.store_id == store_id).order_by(Table.table_number.asc()))
    ).scalars().all()
    orders = (
        await db.execute(
            select(Order)
            .options(selectinload(Order.items).selectinload(OrderItem.options))
            .where(Order.store_id == store_id, Order.status.in_(ACTIVE_STATUSES))
            .order_by(Order.created_at.asc())
        )
    ).scalars().all()

    by_table: dict[int, list[Order]] = {}
    for order in orders:
        by_table.setdefault(order.table_id, []).append(order)

    return [
        TableSummary(table=t, orders=by_table.get(t.id, []), lines=_aggregate(by_table.get(t.id, [])))
        for t in tables
        if t.status is not TableStatus.DISABLED or t.id in by_table
    ]


def _today_start_utc() -> datetime:
    tz = ZoneInfo(settings.BUSINESS_TIMEZONE)
    local_midnight = datetime.combine(datetime.now(tz).date(), time.min, tzinfo=tz)
    return local_midnight.astimezone(timezone.utc)


async def get_today_summary(db: AsyncSession, store_id: int) -> TodaySummary:
    """오늘(영업 시간대 기준) 마감/취소된 주문. 종료 상태는 더 바뀌지 않으므로 updated_at이 마감/취소 시각이다."""
    start = _today_start_utc()
    end = start + timedelta(days=1)
    is_completed = Order.status == OrderStatus.COMPLETED
    row = (
        await db.execute(
            select(
                func.count().filter(is_completed),
                func.coalesce(func.sum(Order.total_price).filter(is_completed), 0),
                func.count().filter(Order.status == OrderStatus.CANCELLED),
            ).where(
                Order.store_id == store_id,
                or_(is_completed, Order.status == OrderStatus.CANCELLED),
                Order.updated_at >= start,
                Order.updated_at < end,
            )
        )
    ).one()
    return TodaySummary(completed_count=row[0], completed_amount=row[1], cancelled_count=row[2])


# =====================================================================
# 일괄 마감
# =====================================================================
async def close_table(
    db: AsyncSession, store_id: int, table_id: int, expected_order_ids: set[int]
) -> tuple[Table, int, int]:
    """테이블의 진행 중 주문을 모두 COMPLETED로 바꾼다. 반환: (테이블, 마감 건수, 마감 금액).
    expected_order_ids: 관리자가 화면에서 확인한 주문 id. 실제 진행 중 주문과 다르면 마감하지 않는다."""
    table = (
        await db.execute(select(Table).where(Table.id == table_id, Table.store_id == store_id))
    ).scalar_one_or_none()
    if not table:
        raise HTTPException(status_code=404, detail="테이블을 찾을 수 없습니다.")

    # 행 잠금: 마감 도중 다른 직원이 같은 주문 상태를 바꾸지 못하게
    orders = (
        await db.execute(
            select(Order)
            .where(Order.table_id == table.id, Order.store_id == store_id, Order.status.in_(ACTIVE_STATUSES))
            .order_by(Order.id)
            .with_for_update()
        )
    ).scalars().all()

    try:
        if not orders:
            raise SettlementError(f"{table.table_number}번 테이블에 마감할 주문이 없습니다.")
        if {o.id for o in orders} != expected_order_ids:
            raise SettlementError(
                f"{table.table_number}번 테이블의 주문이 바뀌었습니다. 내용을 다시 확인한 뒤 마감해 주세요."
            )
        pending = [o for o in orders if o.status is not OrderStatus.SERVED]
        if pending:
            raise SettlementError(
                f"{table.table_number}번 테이블에 아직 서빙 완료되지 않은 주문이 {len(pending)}건 있습니다. "
                "주문 현황에서 처리하거나 취소한 뒤 마감해 주세요."
            )
    except SettlementError:
        await db.rollback()  # 잠금 해제
        raise

    for order in orders:
        validate_transition(order.status, OrderStatus.COMPLETED)
        order.status = OrderStatus.COMPLETED
        # 보드/고객 화면 갱신 신호 (커밋 후 전달)
        await notify_order_changed(db, store_id, table.id, order.id, OrderStatus.COMPLETED)
    await db.commit()
    return table, len(orders), sum(o.total_price for o in orders)
