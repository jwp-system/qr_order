# app/routers/kitchen.py
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.database.models import Order, OrderItem, OrderStatus, Store
from app.database.session import get_db

router = APIRouter(prefix="/kitchen", tags=["Kitchen KDS"])
templates = Jinja2Templates(directory=settings.TEMPLATES_DIR)


async def get_kitchen_orders(db: AsyncSession, store_id: int):
    """주방 처리 대상(주문 확정 대기, 조리 중) 주문 목록 조회"""
    stmt = (
        select(Order)
        .options(
            selectinload(Order.table),
            selectinload(Order.items).selectinload(OrderItem.options)
        )
        .where(
            Order.store_id == store_id,
            Order.status.in_([OrderStatus.ACCEPTED, OrderStatus.COOKING])
        )
        .order_by(Order.created_at.asc())  # 먼저 들어온 순서대로 정렬
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/board", response_class=HTMLResponse)
async def kitchen_board(
    request: Request,
    store_id: int = 1,
    db: AsyncSession = Depends(get_db)
):
    """KDS 메인 대시보드 화면"""
    store_stmt = select(Store).where(Store.id == store_id)
    store_res = await db.execute(store_stmt)
    store = store_res.scalar_one_or_none()
    if not store:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다.")

    orders = await get_kitchen_orders(db, store_id)
    return templates.TemplateResponse(
        request=request,
        name="kitchen/kds_board.html",
        context={
            "store": store, 
            "orders": orders,
            "now": datetime.now(timezone.utc)
        }
    )


@router.get("/feed", response_class=HTMLResponse)
async def kitchen_feed(
    request: Request,
    store_id: int = 1,
    db: AsyncSession = Depends(get_db)
):
    """HTMX 폴링용 주방 주문 카드 그리드 프래그먼트"""
    orders = await get_kitchen_orders(db, store_id)
    return templates.TemplateResponse(
        request=request,
        name="kitchen/fragments/kitchen_ticket_list.html",
        context={"orders": orders, "now": datetime.now(timezone.utc)}
    )


@router.post("/orders/{order_id}/start", response_class=HTMLResponse)
async def start_cooking(
    request: Request,
    order_id: int,
    db: AsyncSession = Depends(get_db)
):
    """조리 시작: ACCEPTED -> COOKING"""
    stmt = (
        select(Order)
        .options(
            selectinload(Order.table),
            selectinload(Order.items).selectinload(OrderItem.options)
        )
        .where(Order.id == order_id)
    )
    res = await db.execute(stmt)
    order = res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="주문을 찾을 수 없습니다.")

    order.status = OrderStatus.COOKING
    await db.commit()
    await db.refresh(order)

    return templates.TemplateResponse(
        request=request,
        name="kitchen/fragments/kitchen_ticket.html",
        context={"order": order, "now": datetime.now(timezone.utc)}
    )


@router.post("/orders/{order_id}/done", response_class=HTMLResponse)
async def finish_cooking(
    order_id: int,
    db: AsyncSession = Depends(get_db)
):
    """조리 완료: COOKING -> COOKED (KDS 화면에서 티켓 제거)"""
    stmt = select(Order).where(Order.id == order_id)
    res = await db.execute(stmt)
    order = res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="주문을 찾을 수 없습니다.")

    order.status = OrderStatus.COOKED
    await db.commit()

    # 조리가 완료되었으므로 주방 보드에서 해당 티켓 DOM 제거
    return HTMLResponse(content="", status_code=200)