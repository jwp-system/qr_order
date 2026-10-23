"""식당용 서비스 (관리자 / 주방 / 홀 공통: 주문 조회 및 상태 전이)"""
from typing import Iterable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app2.core.events import notify_order_changed
from app2.database.models import Order, OrderItem, OrderStatus, Store
from app2.domain.order_status import (
    ACTIVE_STATUSES,  # 라우터에서 svc.ACTIVE_STATUSES로 사용
    ROLE_VIEW_STATUSES,
    STATUS_META,
    InvalidTransition,
    can_role_change,
    stays_on_board,
    validate_transition,
)
from app2.domain.roles import Role

# 역할별 보드 정렬 기준 (없으면 get_orders 기본: 최신순)
BOARD_ORDER_BY = {
    Role.KITCHEN: Order.created_at.asc(),  # 먼저 들어온 순
    Role.HALL: Order.updated_at.asc(),     # 조리 완료된 순
}


# =====================================================================
# 조회
# =====================================================================
def _loader_options():
    return (
        selectinload(Order.table),
        selectinload(Order.items).selectinload(OrderItem.options),
    )


def _order_stmt(order_id: int):
    return select(Order).options(*_loader_options()).where(Order.id == order_id)


async def get_store_or_404(db: AsyncSession, store_id: int) -> Store:
    store = (
        await db.execute(select(Store).where(Store.id == store_id))
    ).scalar_one_or_none()
    if not store:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다.")
    return store


async def get_orders(
    db: AsyncSession,
    store_id: int,
    statuses: Iterable[OrderStatus] = ACTIVE_STATUSES,
    *,
    order_by=None,
) -> list[Order]:
    """매장의 주문 목록 조회. order_by 미지정 시 최신순."""
    stmt = (
        select(Order)
        .options(*_loader_options())
        .where(Order.store_id == store_id, Order.status.in_(list(statuses)))
        .order_by(order_by if order_by is not None else Order.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_board_orders(db: AsyncSession, store_id: int, role: Role) -> list[Order]:
    """역할별 보드 대상 주문 (주방: ACCEPTED/COOKING, 홀: COOKED)."""
    return await get_orders(
        db,
        store_id,
        ROLE_VIEW_STATUSES[role],
        order_by=BOARD_ORDER_BY.get(role),
    )


async def get_order_or_404(
    db: AsyncSession, order_id: int, *, lock: bool = True
) -> Order:
    stmt = _order_stmt(order_id)
    if lock:
        # 동시에 두 직원이 누르는 경우 대비 (DB가 지원할 때 행 잠금)
        stmt = stmt.with_for_update()
    order = (await db.execute(stmt)).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="주문을 찾을 수 없습니다.")
    return order


async def _reload_order(db: AsyncSession, order_id: int) -> Order:
    """커밋 후 템플릿 렌더용으로 table/items/options까지 다시 로드.
    (session.refresh()는 관계 속성을 만료시켜, async에서 렌더 중 lazy load 오류가 날 수 있음)"""
    stmt = _order_stmt(order_id).execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one()


# =====================================================================
# 상태 전이
# =====================================================================
async def _transition(db: AsyncSession, order: Order, target: OrderStatus) -> Order:
    """잠금을 쥔 order를 target으로 전이.
    - 이미 target 상태면 그대로 반환 (더블 탭 대응, 멱등)
    - 허용되지 않은 전이는 409 Conflict
    """
    if order.status == target:
        # 잠금 해제. expire_on_commit=False 라 로드된 값은 그대로 유지된다.
        await db.commit()
        return order

    try:
        validate_transition(order.status, target)
    except InvalidTransition as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    order.status = target
    await notify_order_changed(db, order.store_id, order.id, target)  # 커밋 후 전달
    await db.commit()
    return await _reload_order(db, order.id)


async def change_status(
    db: AsyncSession, order_id: int, target: OrderStatus
) -> Order:
    """조회(잠금) -> 전이 검증 -> 저장."""
    order = await get_order_or_404(db, order_id)
    return await _transition(db, order, target)


async def advance_status(db: AsyncSession, order_id: int) -> Order:
    """STATUS_META.next 기준으로 다음 단계로 진행.
    조회 시 잡은 잠금을 풀지 않고 그대로 전이까지 수행한다."""
    order = await get_order_or_404(db, order_id)
    target = STATUS_META[order.status].next
    if target is None:
        await db.rollback()
        raise HTTPException(status_code=409, detail="더 이상 진행할 수 없는 상태입니다.")
    return await _transition(db, order, target)


async def change_status_by_role(
    db: AsyncSession, order_id: int, target: OrderStatus, role: Role
) -> tuple[Order, bool]:
    """역할 권한 확인 후 상태 변경.
    반환: (order, 변경 후에도 해당 역할 보드에 남는지)"""
    if not can_role_change(role, target):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"'{role.value}' 역할은 {target.value} 상태로 변경할 수 없습니다.",
        )
    order = await change_status(db, order_id, target)
    return order, stays_on_board(role, order.status)