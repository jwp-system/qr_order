# app/routers/client.py
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.database.session import get_db
from app.database.models import (
    Store, Table, Category, Item, OptionGroup, OptionDetail,
    Order, OrderItem, OrderItemOption, OrderStatus
)

router = APIRouter()
templates = Jinja2Templates(directory=settings.TEMPLATES_DIR)


# --- Pydantic Schemas for Order Submission ---
class SelectedOptionIn(BaseModel):
    name: str
    additional_price: int

class CartItemIn(BaseModel):
    item_id: int
    item_name: str
    base_price: int
    quantity: int
    subtotal_price: int
    options: List[SelectedOptionIn] = []

class OrderCreateRequest(BaseModel):
    qr_token: str
    special_instruction: Optional[str] = None
    cart_items: List[CartItemIn]


# --- Status Label & UI Mappings ---
STATUS_INFO = {
    OrderStatus.ORDERED: {"label": "주문 접수", "step": 1, "desc": "주문이 접수되었습니다. 매장 확인 대기 중입니다."},
    OrderStatus.ACCEPTED: {"label": "접수 확인", "step": 1, "desc": "매장에서 주문을 확인했습니다. 곧 조리를 시작합니다."},
    OrderStatus.COOKING: {"label": "조리 중", "step": 2, "desc": "주방에서 맛있게 조리 중입니다."},
    OrderStatus.COOKED: {"label": "조리 완료", "step": 3, "desc": "조리가 완료되었습니다. 서빙을 준비 중입니다."},
    OrderStatus.SERVED: {"label": "서빙 완료", "step": 3, "desc": "음식이 준비되었습니다. 맛있게 드세요!"},
    OrderStatus.COMPLETED: {"label": "식사 완료", "step": 3, "desc": "이용해 주셔서 감사합니다."},
    OrderStatus.CANCELLED: {"label": "주문 취소", "step": 0, "desc": "매장 사정으로 주문이 취소되었습니다."},
}


# ==============================================================================
# Helper / Service Functions (중복 코드 분리)
# ==============================================================================

async def get_active_table_by_token(
    db: AsyncSession, 
    qr_token: str, 
    load_store: bool = False,
    status_code: int = status.HTTP_400_BAD_REQUEST
) -> Table:
    """
    QR 토큰으로 활성화된 테이블을 검증 및 조회하는 공통 함수.
    테이블이 존재하지 않거나 비활성화된 경우 지정된 HTTP 예외를 발생시킵니다.
    """
    stmt = select(Table).where(Table.qr_token == qr_token, Table.is_active.is_(True))
    if load_store:
        stmt = stmt.options(selectinload(Table.store))
        
    result = await db.execute(stmt)
    table = result.scalar_one_or_none()

    if not table:
        detail_msg = "유효하지 않거나 비활성화된 테이블 QR입니다." if status_code == 404 else "유효하지 않은 테이블입니다."
        raise HTTPException(status_code=status_code, detail=detail_msg)

    return table


async def get_item_with_options(db: AsyncSession, item_id: int) -> Item:
    """메뉴 아이템과 하위 옵션 그룹/상세 정보를 함께 로드하는 공통 함수"""
    stmt = (
        select(Item)
        .options(
            selectinload(Item.option_groups).selectinload(OptionGroup.details)
        )
        .where(Item.id == item_id)
    )
    result = await db.execute(stmt)
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="해당 메뉴를 찾을 수 없습니다.")

    return item


# ==============================================================================
# Router Handlers
# ==============================================================================

@router.get("/order/{qr_token}", response_class=HTMLResponse)
async def get_menu_page(
    request: Request,
    qr_token: str,
    db: AsyncSession = Depends(get_db)
):
    """고객 QR 스캔 진입점"""
    # 1. 테이블 및 매장 검증 (공통 함수 사용)
    table = await get_active_table_by_token(
        db, qr_token, load_store=True, status_code=status.HTTP_404_NOT_FOUND
    )
    store = table.store

    # 2. 카테고리 및 메뉴 아이템 로드
    cat_stmt = (
        select(Category)
        .options(selectinload(Category.items))
        .where(Category.store_id == store.id)
        .order_by(Category.display_order.asc())
    )
    cat_result = await db.execute(cat_stmt)
    categories = cat_result.scalars().all()

    # 3. 진행 중인 활성 주문 건수 (배지 표시용)
    count_stmt = (
        select(func.count(Order.id))
        .where(
            Order.table_id == table.id,
            Order.status.in_([
                OrderStatus.ORDERED,
                OrderStatus.ACCEPTED,
                OrderStatus.COOKING,
                OrderStatus.COOKED,
                OrderStatus.SERVED
            ])
        )
    )
    count_result = await db.execute(count_stmt)
    active_order_count = count_result.scalar() or 0

    return templates.TemplateResponse(
        request=request,
        name="client/menu_list.html",
        context={
            "store": store,
            "table": table,
            "categories": categories,
            "qr_token": qr_token,
            "active_order_count": active_order_count
        }
    )


@router.get("/orders/status", response_class=HTMLResponse)
async def get_order_status_modal(
    request: Request,
    qr_token: str,
    db: AsyncSession = Depends(get_db)
):
    """HTMX로 호출되는 주문 현황 모달 프래그먼트"""
    # 1. 테이블 확인 (공통 함수 사용)
    table = await get_active_table_by_token(db, qr_token)

    # 2. 주문 내역 및 품목/옵션 Eager Loading 조회
    orders_stmt = (
        select(Order)
        .options(
            selectinload(Order.items).selectinload(OrderItem.options)
        )
        .where(
            Order.table_id == table.id,
            # Order.status != OrderStatus.CANCELLED,
            Order.status.in_([
                            OrderStatus.ORDERED,
                            OrderStatus.ACCEPTED,
                            OrderStatus.COOKING,
                            OrderStatus.COOKED,
                            OrderStatus.SERVED
                        ])
        )
        .order_by(Order.created_at.desc())
    )
    orders_res = await db.execute(orders_stmt)
    orders = orders_res.scalars().all()

    # 3. UI 렌더링용 속성 매핑
    for ord_obj in orders:
        info = STATUS_INFO.get(ord_obj.status, {"label": "확인 중", "step": 1, "desc": ""})
        ord_obj.status_label = info["label"]
        ord_obj.step = info["step"]
        ord_obj.status_desc = info["desc"]

    total_table_amount = sum(order.total_price for order in orders)

    return templates.TemplateResponse(
        request=request,
        name="client/fragments/order_status_modal.html",
        context={
            "table": table,
            "orders": orders,
            "total_table_amount": total_table_amount
        }
    )


@router.get("/items/{item_id}/modal", response_class=HTMLResponse)
async def get_item_option_modal(
    request: Request,
    item_id: int,
    db: AsyncSession = Depends(get_db)
):
    """메뉴 클릭 시 HTMX로 동적 호출되는 옵션 선택 모달 프래그먼트"""
    # 메뉴 및 하위 옵션 로드 (공통 함수 사용)
    item = await get_item_with_options(db, item_id)

    option_data = {
        "itemId": item.id,
        "itemName": item.name,
        "basePrice": item.price,
        "option_groups": [{
            "groupId": group.id,
            "groupName": group.name if group.name else "",
            "isRequired": group.is_required
        } for group in item.option_groups],
    }
    return templates.TemplateResponse(
        request=request,
        name="client/fragments/option_modal.html",
        context={
            "item": item,
            "option_data": option_data
        }
    )


@router.post("/orders", response_class=HTMLResponse)
async def submit_order(
    request: Request,
    order_data: OrderCreateRequest,
    db: AsyncSession = Depends(get_db)
):
    """장바구니 주문 제출"""
    if not order_data.cart_items:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="장바구니가 비어 있습니다.")

    # 1. 테이블 확인 (공통 함수 사용)
    table = await get_active_table_by_token(db, order_data.qr_token)

    # 2. 총 주문 금액 계산 및 Order 엔티티 생성
    total_price = sum(item.subtotal_price for item in order_data.cart_items)

    new_order = Order(
        store_id=table.store_id,
        table_id=table.id,
        status=OrderStatus.ORDERED,
        special_instruction=order_data.special_instruction,
        total_price=total_price
    )
    db.add(new_order)
    await db.flush()

    # 3. OrderItem & OrderItemOption 스냅샷 적재
    for c_item in order_data.cart_items:
        order_item = OrderItem(
            order_id=new_order.id,
            item_id=c_item.item_id,
            item_name=c_item.item_name,
            base_price=c_item.base_price,
            quantity=c_item.quantity,
            subtotal_price=c_item.subtotal_price
        )
        db.add(order_item)
        await db.flush()

        for opt in c_item.options:
            order_item_opt = OrderItemOption(
                order_item_id=order_item.id,
                option_name=opt.name,
                additional_price=opt.additional_price
            )
            db.add(order_item_opt)

    await db.commit()
    await db.refresh(new_order)

    # 4. 주문 완료 모달 프래그먼트 반환
    return templates.TemplateResponse(
        request=request,
        name="client/fragments/order_completed.html",
        context={
            "order": new_order,
            "table_number": table.table_number
        }
    )