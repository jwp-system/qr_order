from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import now, templates
from app.database.models import Order, OrderStatus
from app.database.session import get_db
from app.services import order_service as svc

router = APIRouter(prefix="/admin", tags=["Store Admin"])
S = OrderStatus


def _card(request: Request, order: Order) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="store_admin/fragments/order_card.html",
        context={"order": order, "now": now()},
    )


@router.get("/orders", response_class=HTMLResponse)
async def admin_orders_page(
    request: Request,
    store_id: int = 1,  # MVP용 기본 매장 ID (인증 연동 전)
    db: AsyncSession = Depends(get_db),
):
    store = await svc.get_store_or_404(db, store_id)
    orders = await svc.get_orders(db, store_id)
    return templates.TemplateResponse(
        request=request,
        name="store_admin/order_board.html",
        context={"store": store, "orders": orders, "now": now()},
    )


@router.get("/orders/feed", response_class=HTMLResponse)
async def admin_orders_feed(
    request: Request,
    store_id: int = 1,
    order_status: Optional[OrderStatus] = None,  # ?order_status=COOKING
    db: AsyncSession = Depends(get_db),
):
    statuses = [order_status] if order_status else svc.ACTIVE_STATUSES
    orders = await svc.get_orders(db, store_id, statuses)
    return templates.TemplateResponse(
        request=request,
        name="store_admin/fragments/order_list.html",
        context={"orders": orders, "now": now()},
    )


@router.post("/orders/{order_id}/accept", response_class=HTMLResponse)
async def accept_order(request: Request, order_id: int, db: AsyncSession = Depends(get_db)):
    """ORDERED -> ACCEPTED"""
    return _card(request, await svc.change_status(db, order_id, S.ACCEPTED))


@router.post("/orders/{order_id}/advance", response_class=HTMLResponse)
async def advance_order(request: Request, order_id: int, db: AsyncSession = Depends(get_db)):
    """STATUS_META.next 기준 다음 단계로 (관리자 수동 진행용)"""
    return _card(request, await svc.advance_status(db, order_id))


@router.post("/orders/{order_id}/cancel", response_class=HTMLResponse)
async def cancel_order(request: Request, order_id: int, db: AsyncSession = Depends(get_db)):
    """-> CANCELLED"""
    return _card(request, await svc.change_status(db, order_id, S.CANCELLED))


@router.post("/orders/{order_id}/complete", response_class=HTMLResponse)
async def complete_order(order_id: int, db: AsyncSession = Depends(get_db)):
    """SERVED -> COMPLETED (보드에서 카드 제거)"""
    await svc.change_status(db, order_id, S.COMPLETED)
    return HTMLResponse(content="", status_code=200)