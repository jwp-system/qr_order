from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import now, templates
from app.database.models import Order, OrderStatus
from app.database.session import get_db
from app.domain.order_status import HALL_STATUSES
from app.services import order_service as svc

router = APIRouter(prefix="/hall", tags=["Hall Serving"])
S = OrderStatus


async def _serving_orders(db: AsyncSession, store_id: int) -> list[Order]:
    """서빙 대기(COOKED). 조리 완료된 순서대로."""
    return await svc.get_orders(
        db, store_id, HALL_STATUSES, order_by=Order.updated_at.asc()
    )


@router.get("/serving", response_class=HTMLResponse)
async def hall_board(request: Request, store_id: int = 1, db: AsyncSession = Depends(get_db)):
    store = await svc.get_store_or_404(db, store_id)
    orders = await _serving_orders(db, store_id)
    return templates.TemplateResponse(
        request=request,
        name="hall/serving_board.html",
        context={"store": store, "orders": orders, "now": now()},
    )


@router.get("/feed", response_class=HTMLResponse)
async def hall_feed(request: Request, store_id: int = 1, db: AsyncSession = Depends(get_db)):
    orders = await _serving_orders(db, store_id)
    return templates.TemplateResponse(
        request=request,
        name="hall/fragments/serving_ticket_list.html",
        context={"orders": orders, "now": now()},
    )


@router.post("/orders/{order_id}/serve", response_class=HTMLResponse)
async def complete_serving(order_id: int, db: AsyncSession = Depends(get_db)):
    """서빙 완료: COOKED -> SERVED (홀 화면에서 티켓 제거)"""
    await svc.change_status(db, order_id, S.SERVED)
    return HTMLResponse(content="", status_code=200)