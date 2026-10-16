from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import templates
from app.database.session import get_db
from app.services import client_service as svc

router = APIRouter(tags=["Client"])


# --- Schemas ---
class SelectedOptionIn(BaseModel):
    name: str
    additional_price: int = Field(ge=0)


class CartItemIn(BaseModel):
    item_id: int
    item_name: str
    base_price: int = Field(ge=0)
    quantity: int = Field(ge=1, le=99)
    subtotal_price: int = Field(ge=0)
    options: List[SelectedOptionIn] = []


class OrderCreateRequest(BaseModel):
    qr_token: str
    special_instruction: Optional[str] = Field(default=None, max_length=200)
    cart_items: List[CartItemIn]


# --- Handlers ---
@router.get("/order/{qr_token}", response_class=HTMLResponse)
async def get_menu_page(request: Request, qr_token: str, db: AsyncSession = Depends(get_db)):
    """고객 QR 스캔 진입점"""
    table = await svc.get_active_table_by_token(
        db, qr_token, load_store=True, not_found_status=status.HTTP_404_NOT_FOUND
    )
    store = table.store
    categories = await svc.get_menu_categories(db, store.id)
    active_order_count = await svc.count_table_active_orders(db, table.id)

    return templates.TemplateResponse(
        request=request,
        name="client/menu_list.html",
        context={
            "store": store,
            "table": table,
            "categories": categories,
            "qr_token": qr_token,
            "active_order_count": active_order_count,
        },
    )


@router.get("/orders/status", response_class=HTMLResponse)
async def get_order_status_modal(request: Request, qr_token: str, db: AsyncSession = Depends(get_db)):
    """주문 현황 모달 (HTMX 프래그먼트)"""
    table = await svc.get_active_table_by_token(db, qr_token)
    orders = await svc.get_table_orders(db, table.id)

    return templates.TemplateResponse(
        request=request,
        name="client/fragments/order_status_modal.html",
        context={
            "table": table,
            "orders": orders,
            "total_table_amount": sum(o.total_price for o in orders),
        },
    )


@router.get("/items/{item_id}/modal", response_class=HTMLResponse)
async def get_item_option_modal(request: Request, item_id: int, db: AsyncSession = Depends(get_db)):
    """옵션 선택 모달 (HTMX 프래그먼트)"""
    item = await svc.get_item_with_options(db, item_id)
    return templates.TemplateResponse(
        request=request,
        name="client/fragments/option_modal.html",
        context={"item": item, "option_data": svc.build_option_modal_data(item)},
    )


@router.post("/orders", response_class=HTMLResponse)
async def submit_order(
    request: Request, order_data: OrderCreateRequest, db: AsyncSession = Depends(get_db)
):
    """장바구니 주문 제출"""
    table = await svc.get_active_table_by_token(db, order_data.qr_token)
    order = await svc.create_order(
        db, table, order_data.cart_items, order_data.special_instruction
    )

    return templates.TemplateResponse(
        request=request,
        name="client/fragments/order_completed.html",
        context={"order": order, "table_number": table.table_number},
    )