from typing import Iterable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Order, OrderItem, OrderStatus, Store
from app.domain.order_status import (
    ACTIVE_STATUSES,
    STATUS_META,
    InvalidTransition,
    validate_transition,
)


def _loader_options():
    return (
        selectinload(Order.table),
        selectinload(Order.items).selectinload(OrderItem.options),
    )


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


async def get_order_or_404(db: AsyncSession, order_id: int) -> Order:
    stmt = (
        select(Order)
        .options(*_loader_options())
        .where(Order.id == order_id)
        # 동시에 두 직원이 누르는 경우 대비 (DB가 지원할 때 행 잠금)
        .with_for_update()
    )
    order = (await db.execute(stmt)).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="주문을 찾을 수 없습니다.")
    return order


async def change_status(
    db: AsyncSession, order_id: int, target: OrderStatus
) -> Order:
    """조회 -> 전이 검증 -> 저장.
    - 이미 target 상태면 그대로 반환 (더블 탭 대응, 멱등)
    - 허용되지 않은 전이는 409 Conflict
    """
    order = await get_order_or_404(db, order_id)

    if order.status == target:
        await db.rollback()  # 잠금 해제
        return order

    try:
        validate_transition(order.status, target)
    except InvalidTransition as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    order.status = target
    await db.commit()
    await db.refresh(order)
    return order


async def advance_status(db: AsyncSession, order_id: int) -> Order:
    """STATUS_META.next 기준으로 다음 단계로 진행."""
    order = await get_order_or_404(db, order_id)
    target = STATUS_META[order.status].next
    await db.rollback()  # 아래 change_status에서 다시 잠금
    if target is None:
        raise HTTPException(status_code=409, detail="더 이상 진행할 수 없는 상태입니다.")
    return await change_status(db, order_id, target)