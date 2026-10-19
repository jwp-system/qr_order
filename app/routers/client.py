"""고객용 API (QR 스캔 → 메뉴 → 주문)"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import templates
from app.database.session import get_db
from app.services import client_service as svc

router = APIRouter(tags=["Client Order"])


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
    special_instruction: Optional[str] = Field(default=None, max_length=100)  # Order.special_instruction = String(100)
    cart_items: List[CartItemIn]


# --- Pages ---
@router.get("/order/{qr_token}", response_class=HTMLResponse)
async def get_menu_page(request: Request, qr_token: str, db: AsyncSession = Depends(get_db)):
    """고객 QR 스캔 진입점"""
    return templates.TemplateResponse(
        request=request,
        name="client/order.html",
        context=await svc.get_menu_context(db, qr_token),
    )


# --- HTMX fragments ---
@router.get("/orders/status", response_class=HTMLResponse)
async def get_order_status_modal(request: Request, qr_token: str, db: AsyncSession = Depends(get_db)):
    """주문 현황 모달 (HTMX 프래그먼트)"""
    return templates.TemplateResponse(
        request=request,
        name="client/fragments/order_status_modal.html",
        context=await svc.get_order_status_context(db, qr_token),
    )


@router.get("/items/{item_id}/modal", response_class=HTMLResponse)
async def get_item_option_modal(request: Request, item_id: int, db: AsyncSession = Depends(get_db)):
    """옵션 선택 모달 (HTMX 프래그먼트)"""
    return templates.TemplateResponse(
        request=request,
        name="client/fragments/option_modal.html",
        context=await svc.get_item_modal_context(db, item_id),
    )


# --- Actions ---
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