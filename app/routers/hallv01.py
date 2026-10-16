# app/routers/hall.py
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

router = APIRouter(prefix="/hall", tags=["Hall Serving"])
templates = Jinja2Templates(directory=settings.TEMPLATES_DIR)


async def get_serving_orders(db: AsyncSession, store_id: int):
    """조리 완료 후 서빙 대기 중(COOKED)인 주문 목록 조회"""
    stmt = (
        select(Order)
        .options(
            selectinload(Order.table),
            selectinload(Order.items).selectinload(OrderItem.options)
        )
        .where(Order.store_id == store_id, Order.status == OrderStatus.COOKED)
        .order_by(Order.updated_at.asc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/serving", response_class=HTMLResponse)
async def hall_board(
    request: Request,
    store_id: int = 1,
    db: AsyncSession = Depends(get_db)
):
    """홀 서빙 메인 화면"""
    store_stmt = select(Store).where(Store.id == store_id)
    store_res = await db.execute(store_stmt)
    store = store_res.scalar_one_or_none()
    if not store:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다.")

    orders = await get_serving_orders(db, store_id)
    return templates.TemplateResponse(
        request=request,
        name="hall/serving_board.html",
        context={"store": store, "orders": orders}
    )


@router.get("/feed", response_class=HTMLResponse)
async def hall_feed(
    request: Request,
    store_id: int = 1,
    db: AsyncSession = Depends(get_db)
):
    """HTMX 폴링용 서빙 목록 프래그먼트"""
    orders = await get_serving_orders(db, store_id)
    return templates.TemplateResponse(
        request=request,
        name="hall/fragments/serving_ticket_list.html",
        context={"orders": orders}
    )


@router.post("/orders/{order_id}/serve", response_class=HTMLResponse)
async def complete_serving(
    order_id: int,
    db: AsyncSession = Depends(get_db)
):
    """서빙 완료: COOKED -> SERVED (홀 화면에서 티켓 제거)"""
    stmt = select(Order).where(Order.id == order_id)
    res = await db.execute(stmt)
    order = res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="주문을 찾을 수 없습니다.")

    order.status = OrderStatus.SERVED
    await db.commit()

    # 홀 화면에서 서빙 완료된 주문 DOM 제거
    return HTMLResponse(content="", status_code=200)