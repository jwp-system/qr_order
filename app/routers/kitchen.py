from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import now, templates
from app.database.models import Order, OrderStatus
from app.database.session import get_db
from app.domain.order_status import KITCHEN_STATUSES
from app.services import order_service as svc

router = APIRouter(prefix="/kitchen", tags=["Kitchen KDS"])
S = OrderStatus


async def _kitchen_orders(db: AsyncSession, store_id: int) -> list[Order]:
    """주방 대상(ACCEPTED, COOKING). 먼저 들어온 순."""
    return await svc.get_orders(
        db, store_id, KITCHEN_STATUSES, order_by=Order.created_at.asc()
    )


@router.get("/board", response_class=HTMLResponse)
async def kitchen_board(request: Request, store_id: int = 1, db: AsyncSession = Depends(get_db)):
    store = await svc.get_store_or_404(db, store_id)
    orders = await _kitchen_orders(db, store_id)
    return templates.TemplateResponse(
        request=request,
        name="kitchen/kds_board.html",
        context={"store": store, "orders": orders, "now": now()},
    )


@router.get("/feed", response_class=HTMLResponse)
async def kitchen_feed(request: Request, store_id: int = 1, db: AsyncSession = Depends(get_db)):
    orders = await _kitchen_orders(db, store_id)
    return templates.TemplateResponse(
        request=request,
        name="kitchen/fragments/kitchen_ticket_list.html",
        context={"orders": orders, "now": now()},
    )


@router.post("/orders/{order_id}/start", response_class=HTMLResponse)
async def start_cooking(request: Request, order_id: int, db: AsyncSession = Depends(get_db)):
    """조리 시작: ACCEPTED -> COOKING"""
    order = await svc.change_status(db, order_id, S.COOKING)
    return templates.TemplateResponse(
        request=request,
        name="kitchen/fragments/kitchen_ticket.html",
        context={"order": order, "now": now()},
    )


@router.post("/orders/{order_id}/done", response_class=HTMLResponse)
async def finish_cooking(order_id: int, db: AsyncSession = Depends(get_db)):
    """조리 완료: COOKING -> COOKED (KDS에서 티켓 제거)"""
    await svc.change_status(db, order_id, S.COOKED)
    return HTMLResponse(content="", status_code=200)