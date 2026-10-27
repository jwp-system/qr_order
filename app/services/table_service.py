"""매장 관리자 테이블 관리 서비스 (추가 / 번호 수정 / 상태 / QR 재발급 / 삭제).

모든 조회는 로그인 세션의 store_id로 범위를 제한한다 (다른 매장 테이블은 404).
폼 값 검증 실패나 허용되지 않는 변경은 TableError(메시지)로 올리고, 라우터가 목록을 다시 그려 보여준다.
"""
import io
import secrets
from collections.abc import Mapping
from dataclasses import dataclass

import qrcode
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order, Table, TableStatus
from app.domain.order_status import ACTIVE_STATUSES

MAX_TABLE_NUMBER = 9_999
MAX_ADD_AT_ONCE = 50


class TableError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class TableRow:
    table: Table
    active_orders: int  # 진행 중 주문 수
    has_orders: bool    # 주문 이력이 있는지 (있으면 삭제 불가)


def new_qr_token() -> str:
    """추측 불가능한 테이블 QR 토큰. 테이블 번호는 바뀔 수 있으므로 넣지 않는다."""
    return secrets.token_urlsafe(16)


def qr_png(url: str) -> bytes:
    img = qrcode.make(url, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _number(form: Mapping, key: str, label: str, *, default: int | None = None, maximum: int = MAX_TABLE_NUMBER) -> int:
    raw = (form.get(key) or "").strip()
    if not raw and default is not None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise TableError(f"{label}은(는) 숫자로 입력해 주세요.") from None
    if not 1 <= value <= maximum:
        raise TableError(f"{label}은(는) 1 ~ {maximum:,} 사이로 입력해 주세요.")
    return value


# =====================================================================
# 조회
# =====================================================================
async def list_tables(db: AsyncSession, store_id: int) -> list[TableRow]:
    tables = (
        await db.execute(select(Table).where(Table.store_id == store_id).order_by(Table.table_number.asc()))
    ).scalars().all()
    counts = (
        await db.execute(
            select(
                Order.table_id,
                func.count().filter(Order.status.in_(ACTIVE_STATUSES)),
                func.count(),
            )
            .where(Order.store_id == store_id)
            .group_by(Order.table_id)
        )
    ).all()
    by_table = {table_id: (active, total) for table_id, active, total in counts}
    return [
        TableRow(table=t, active_orders=by_table.get(t.id, (0, 0))[0], has_orders=t.id in by_table)
        for t in tables
    ]


async def get_table(db: AsyncSession, store_id: int, table_id: int) -> Table:
    table = (
        await db.execute(select(Table).where(Table.id == table_id, Table.store_id == store_id))
    ).scalar_one_or_none()
    if not table:
        raise HTTPException(status_code=404, detail="테이블을 찾을 수 없습니다.")
    return table


async def _taken_numbers(db: AsyncSession, store_id: int) -> set[int]:
    return set((await db.execute(select(Table.table_number).where(Table.store_id == store_id))).scalars().all())


async def _commit_numbers(db: AsyncSession) -> None:
    """번호 중복 검사와 저장 사이에 다른 요청이 같은 번호를 넣은 경우 (uq_store_table_number)"""
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise TableError("방금 다른 곳에서 같은 테이블 번호가 등록되었습니다. 다시 시도해 주세요.") from None


async def _active_order_count(db: AsyncSession, table: Table) -> int:
    return (
        await db.execute(
            select(func.count()).where(Order.table_id == table.id, Order.status.in_(ACTIVE_STATUSES))
        )
    ).scalar_one()


# =====================================================================
# 변경
# =====================================================================
async def add_tables(db: AsyncSession, store_id: int, form: Mapping) -> list[int]:
    """start번부터 count개 추가 (start 미입력 시 마지막 번호 다음부터). 추가된 번호 목록을 돌려준다."""
    taken = await _taken_numbers(db, store_id)
    start = _number(form, "start", "시작 번호", default=max(taken, default=0) + 1)
    count = _number(form, "count", "개수", default=1, maximum=MAX_ADD_AT_ONCE)
    numbers = list(range(start, start + count))
    if numbers[-1] > MAX_TABLE_NUMBER:
        raise TableError(f"테이블 번호는 {MAX_TABLE_NUMBER:,}번까지 쓸 수 있습니다.")
    dup = sorted(taken.intersection(numbers))
    if dup:
        raise TableError(f"이미 있는 테이블 번호입니다: {', '.join(map(str, dup[:10]))}{' ...' if len(dup) > 10 else ''}")

    db.add_all(Table(store_id=store_id, table_number=n, qr_token=new_qr_token()) for n in numbers)
    await _commit_numbers(db)
    return numbers


async def update_number(db: AsyncSession, table: Table, form: Mapping) -> None:
    number = _number(form, "table_number", "테이블 번호")
    if number != table.table_number and number in await _taken_numbers(db, table.store_id):
        raise TableError(f"{number}번 테이블이 이미 있습니다.")
    table.table_number = number
    await _commit_numbers(db)


async def set_status(db: AsyncSession, table: Table, status: TableStatus) -> None:
    # '사용 안 함'이면 고객이 주문 내역을 볼 수 없으므로, 진행 중 주문은 정산/취소 후에 끈다
    if status is TableStatus.DISABLED and await _active_order_count(db, table):
        raise TableError(f"{table.table_number}번 테이블에 진행 중인 주문이 있습니다. 정산(마감)하거나 취소한 뒤 변경해 주세요.")
    table.status = status
    await db.commit()


async def reissue_qr(db: AsyncSession, table: Table) -> None:
    """QR 토큰 재발급. 기존에 인쇄한 QR은 더 이상 동작하지 않는다."""
    table.qr_token = new_qr_token()
    await db.commit()


async def delete_table(db: AsyncSession, table: Table) -> None:
    # 주문이 테이블을 참조하므로(지난 주문 내역 보존) 주문 이력이 있으면 삭제 대신 '사용 안 함'으로
    has_orders = (await db.execute(select(func.count()).where(Order.table_id == table.id))).scalar_one()
    if has_orders:
        raise TableError(f"{table.table_number}번 테이블은 주문 이력이 있어 삭제할 수 없습니다. '사용 안 함'으로 바꿔 주세요.")
    await db.delete(table)
    await db.commit()
