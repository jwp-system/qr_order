# app/routers/store_admin.py
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.database.session import get_db
from app.database.models import Store, Order, OrderItem, OrderItemOption, OrderStatus, Table

router = APIRouter(prefix="/admin", tags=["Store Admin"])
templates = Jinja2Templates(directory=settings.TEMPLATES_DIR)

# ==============================================================================
# Helper / Service Functions (중복 코드 분리)
# ==============================================================================
async def get_orders(db: AsyncSession, store_id: int, order_step: int = -1):
    """현재 진행 중인 주문 목록 조회 (테이블, 아이템, 옵션 Eager Loading)"""
    """주문 접수 -> 조리 대기 -> 조리 중 -> 서빙 준비-> 서빙 완료"""
    order_status = [
                OrderStatus.ORDERED,
                OrderStatus.ACCEPTED,
                OrderStatus.COOKING,
                OrderStatus.COOKED,
                OrderStatus.SERVED
    ]
    if order_step == 1:
        order_status = [OrderStatus.ORDERED]
    elif order_step == 2:
        order_status = [OrderStatus.ACCEPTED]
    elif order_status == 3:
        order_status = [OrderStatus.COOKING]
    elif order_status == 4:
        order_status = [OrderStatus.COOKED]
    elif order_status == 5:
        order_status = [OrderStatus.SERVED]
        # order_status.append(OrderStatus.COMPLETED)  # 완료 주문은 보드에서 제외
    stmt = (
        select(Order)
        .options(
            selectinload(Order.table),
            selectinload(Order.items).selectinload(OrderItem.options)
        )
        .where(
            Order.store_id == store_id,
            Order.status.in_(order_status)
        )
        .order_by(Order.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_order_with_id(db: AsyncSession, order_id: int) -> Order:
    """주문과 관련된 정보를 함께 로드하는 공통 함수"""
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

    return order


# ==============================================================================
# Router Handlers
# ==============================================================================

@router.get("/orders", response_class=HTMLResponse)
async def admin_orders_page(
    request: Request,
    store_id: int = 1,  # MVP용 기본 매장 ID (인증 세션 연동 전)
    db: AsyncSession = Depends(get_db)
):
    """매장 관리자 주문 접수/관리 메인 화면"""
    store_stmt = select(Store).where(Store.id == store_id)
    store_res = await db.execute(store_stmt)
    store = store_res.scalar_one_or_none()
    if not store:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다.")
    ## 전체 주문
    orders = await get_orders(db, store_id)

    return templates.TemplateResponse(
        request=request,
        name="store_admin/order_board.html",
        context={
            "store": store,
            "orders": orders,
            "now": datetime.now(timezone.utc)
        }
    )


@router.get("/orders/feed", response_class=HTMLResponse)
async def admin_orders_feed(
    request: Request,
    store_id: int = 1,
    order_step: int = -1,
    db: AsyncSession = Depends(get_db)
):
    """HTMX 폴링(hx-trigger='every 10s')으로 주문 목록 영역만 동적 갱신"""
    orders = await get_orders(db, store_id, order_step)
    return templates.TemplateResponse(
        request=request,
        name=
        "store_admin/fragments/order_list.html",
        context={
            "orders": orders,
            "now": datetime.now(timezone.utc)
        }
    )


@router.post("/orders/{order_id}/accept", response_class=HTMLResponse)
async def accept_order(
    request: Request,
    order_id: int,
    db: AsyncSession = Depends(get_db)
):
    """주문 확정: ORDERED -> ACCEPTED (주방 전달 상태)"""
    order = await get_order_with_id(db, order_id)
    order.status = OrderStatus.ACCEPTED
    await db.commit()
    await db.refresh(order)

    # 개별 카드 조각만 교체 반환
    return templates.TemplateResponse(
        request=request,
        name=
        "store_admin/fragments/order_card.html",
        context={
            "order": order, "now": datetime.now(timezone.utc)
        }
    )


@router.post("/orders/{order_id}/cancel", response_class=HTMLResponse)
async def cancel_order(
    request: Request,
    order_id: int,
    db: AsyncSession = Depends(get_db)
):
    """주문 취소/반려: -> CANCELLED"""
    order = await get_order_with_id(db, order_id)
    order.status = OrderStatus.CANCELLED
    await db.commit()
    await db.refresh(order)

    # 취소된 카드는 흐리게 표시하거나 취소 뱃지 렌더링
    return templates.TemplateResponse(
        request=request,
        name=
        "store_admin/fragments/order_card.html",
        context={
            "order": order, "now": datetime.now(timezone.utc)
        }
    )


@router.post("/orders/{order_id}/complete", response_class=HTMLResponse)
async def complete_order(
    request: Request,
    order_id: int,
    db: AsyncSession = Depends(get_db)
):
    """최종 서빙 및 결제 확인 완료: -> COMPLETED (보드에서 제거)"""
    stmt = select(Order).where(Order.id == order_id)
    res = await db.execute(stmt)
    order = res.scalar_one_or_none()

    if not order:
        raise HTTPException(status_code=404, detail="주문을 찾을 수 없습니다.")

    order.status = OrderStatus.COMPLETED
    await db.commit()

    # 완료 시 보드 목록에서 해당 카드 DOM 제거 (빈 응답 반환)
    return HTMLResponse(content="", status_code=200)