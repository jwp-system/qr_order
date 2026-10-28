"""고객용 API (QR 스캔 → 메뉴 → 주문)

데모 매장(랜딩 '주문 입력 테스트')이면 손님 화면에 데모 안내를 띄우고, 주문 횟수/수량을 제한한다.
"""
import math
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import FailureLimiter
from app.core.templating import templates
from app.database.session import get_db
from app.services import client_service as svc
from app.services import demo_service

router = APIRouter(tags=["Client Order"])

# 데모 매장: 같은 IP에서 10분에 10건까지 주문
_demo_order_limiter = FailureLimiter(max_failures=10, window_seconds=600, lock_seconds=600)


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
    context = await svc.get_menu_context(db, qr_token)
    context["is_demo"] = demo_service.is_demo(context["store"].id, await demo_service.demo_store(db))
    return templates.TemplateResponse(request=request, name="client/order.html", context=context)


# --- HTMX fragments ---
@router.get("/orders/status", response_class=HTMLResponse)
async def get_order_status_modal(request: Request, qr_token: str, db: AsyncSession = Depends(get_db)):
    """주문 현황 모달 (HTMX 프래그먼트)"""
    return templates.TemplateResponse(
        request=request,
        name="client/fragments/order_status_modal.html",
        context=await svc.get_order_status_context(db, qr_token),
    )


@router.get("/orders/active", response_class=HTMLResponse)
async def get_order_active_count(request: Request, qr_token: str, db: AsyncSession = Depends(get_db)):
    """진행 중 주문 건수 배지 (HTMX 프래그먼트): 주문 변경 이벤트 수신 시 호출"""
    return templates.TemplateResponse(
        request=request,
        name="client/fragments/order_status_active.html",
        context=await svc.get_active_order_count(db, qr_token),
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
    if demo_service.is_demo(table.store_id, await demo_service.demo_store(db)):
        demo_service.check_order_limits(order_data.cart_items)
        ip = request.client.host if request.client else "unknown"
        wait = _demo_order_limiter.retry_after(ip)
        if wait:
            raise HTTPException(status_code=429, detail=f"데모 주문이 많습니다. {math.ceil(wait / 60)}분 뒤에 다시 시도해 주세요.")
        _demo_order_limiter.record_failure(ip)  # 주문 횟수로 센다
    order = await svc.create_order(
        db, table, order_data.cart_items, order_data.special_instruction
    )
    return templates.TemplateResponse(
        request=request,
        name="client/fragments/order_completed.html",
        context={"order": order, "table_number": table.table_number},
    )
