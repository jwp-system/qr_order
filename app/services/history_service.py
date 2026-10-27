"""주문 내역 조회 (매장 관리자, 서비스 종료 후 조회 기간 포함).

날짜는 영업 시간대 기준 영업일(core/clock.py)이고 주문 시각(created_at)으로 거른다.
화면은 페이지 단위로, CSV는 같은 조건 전체(최대 CSV_LIMIT건)를 내려준다.
"""
import csv
import io
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.clock import business_tz, day_start_utc
from app.database.models import Order, OrderItem, OrderStatus, Table
from app.domain.order_status import ACTIVE_STATUSES, STATUS_META

PAGE_SIZE = 50
DEFAULT_DAYS = 7
MAX_RANGE_DAYS = 366
CSV_LIMIT = 20_000

# 상태 필터: (키, 표시명, 대상 상태 | None=전체)
STATUS_FILTERS: list[tuple[str, str, tuple[OrderStatus, ...] | None]] = [
    ("all", "전체", None),
    ("completed", "마감", (OrderStatus.COMPLETED,)),
    ("cancelled", "취소", (OrderStatus.CANCELLED,)),
    ("active", "진행 중", tuple(ACTIVE_STATUSES)),
]
_STATUS_BY_KEY = {key: statuses for key, _, statuses in STATUS_FILTERS}


@dataclass
class HistoryFilter:
    date_from: date
    date_to: date
    status: str = "all"
    table_number: int | None = None
    page: int = 1

    def query_string(self, **override) -> str:
        """같은 조건으로 다른 페이지/CSV 링크 만들 때"""
        values = {
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
            "status": self.status,
            "table": self.table_number or "",
            "page": self.page,
        }
        values.update(override)
        return "&".join(f"{k}={v}" for k, v in values.items() if v not in ("", None))


@dataclass
class HistoryResult:
    orders: list[Order]
    total_count: int
    completed_count: int
    completed_amount: int
    cancelled_count: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total_count // PAGE_SIZE))


def parse_filter(params: Mapping, default_to: date) -> tuple[HistoryFilter, str | None]:
    """쿼리 값 -> 조건. 잘못된 값은 기본값으로 바꾸고 안내 문구를 돌려준다."""
    error = None

    def _date(key: str, fallback: date) -> date:
        nonlocal error
        raw = (params.get(key) or "").strip()
        if not raw:
            return fallback
        try:
            return date.fromisoformat(raw)
        except ValueError:
            error = "날짜 형식이 올바르지 않아 기본 기간으로 조회했습니다."
            return fallback

    date_to = _date("date_to", default_to)
    date_from = _date("date_from", date_to - timedelta(days=DEFAULT_DAYS - 1))
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    if (date_to - date_from).days >= MAX_RANGE_DAYS:
        date_from = date_to - timedelta(days=MAX_RANGE_DAYS - 1)
        error = f"조회 기간은 최대 {MAX_RANGE_DAYS}일입니다. 시작일을 조정했습니다."

    status = params.get("status") or "all"
    if status not in _STATUS_BY_KEY:
        status = "all"

    table_number = None
    raw_table = (params.get("table") or "").strip()
    if raw_table.isdigit():
        table_number = int(raw_table)

    page = params.get("page") or "1"
    page = int(page) if str(page).isdigit() and int(page) > 0 else 1
    return HistoryFilter(date_from, date_to, status, table_number, page), error


def _where(store_id: int, f: HistoryFilter) -> list:
    conditions = [
        Order.store_id == store_id,
        Order.created_at >= day_start_utc(f.date_from),
        Order.created_at < day_start_utc(f.date_to + timedelta(days=1)),
    ]
    statuses = _STATUS_BY_KEY[f.status]
    if statuses:
        conditions.append(Order.status.in_(statuses))
    if f.table_number is not None:
        conditions.append(Order.table_id.in_(select(Table.id).where(
            Table.store_id == store_id, Table.table_number == f.table_number,
        )))
    return conditions


def _orders_stmt(store_id: int, f: HistoryFilter):
    return (
        select(Order)
        .options(selectinload(Order.table), selectinload(Order.items).selectinload(OrderItem.options))
        .where(*_where(store_id, f))
        .order_by(Order.created_at.desc(), Order.id.desc())
    )


async def search(db: AsyncSession, store_id: int, f: HistoryFilter) -> HistoryResult:
    is_completed = Order.status == OrderStatus.COMPLETED
    total, completed, amount, cancelled = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(is_completed),
                func.coalesce(func.sum(Order.total_price).filter(is_completed), 0),
                func.count().filter(Order.status == OrderStatus.CANCELLED),
            ).where(*_where(store_id, f))
        )
    ).one()
    orders = (
        await db.execute(_orders_stmt(store_id, f).offset((f.page - 1) * PAGE_SIZE).limit(PAGE_SIZE))
    ).scalars().all()
    return HistoryResult(list(orders), total, completed, amount, cancelled)


async def export_csv(db: AsyncSession, store_id: int, f: HistoryFilter) -> str:
    """같은 조건의 주문 내역 CSV (엑셀에서 한글이 깨지지 않도록 BOM 포함). 메뉴 한 줄 = CSV 한 행."""
    orders = (await db.execute(_orders_stmt(store_id, f).limit(CSV_LIMIT))).scalars().all()
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    w.writerow(["주문번호", "주문시각", "테이블", "상태", "메뉴", "옵션", "수량", "메뉴금액", "주문합계", "요청사항"])
    tz = business_tz()
    for o in orders:
        base = [o.id, o.created_at.astimezone(tz).strftime("%Y-%m-%d %H:%M"), o.table.table_number, STATUS_META[o.status].label]
        tail = [o.total_price, o.special_instruction or ""]
        for item in o.items or [None]:
            if item is None:
                w.writerow([*base, "", "", "", "", *tail])
            else:
                options = ", ".join(opt.option_name for opt in item.options)
                w.writerow([*base, item.item_name, options, item.quantity, item.subtotal_price, *tail])
    return buf.getvalue()
