"""식당용 API (관리자 / 주방 KDS / 홀 서빙)

  /admin/...   매장 관리자 (주문 현황 + 상태 변경)
  /kitchen/... 주방 보드
  /hall/...    홀 서빙 보드

모든 상태 변경은 공통 카드 버튼이 호출하는 POST /admin/orders/{id}/advance 하나로 처리한다
(역할 권한 + 전이 규칙 검증은 services/store_service.change_status_by_role).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app2.core.board_config import BOARDS
from app2.core.templating import now, render_card, templates
from app2.database.models import OrderStatus
from app2.database.session import get_db
from app2.domain.roles import Role
from app2.services import store_service as svc

admin_router = APIRouter(prefix="/admin", tags=["Store Admin"])
kitchen_router = APIRouter(prefix="/kitchen", tags=["Kitchen KDS"])
hall_router = APIRouter(prefix="/hall", tags=["Hall Serving"])

# main.py에서는 이 router 하나만 include 하면 됩니다.
router = APIRouter()


def _empty() -> HTMLResponse:
    """카드/티켓을 보드에서 제거 (HTMX swap용 빈 응답)"""
    return HTMLResponse(content="", status_code=200)


# =====================================================================
# Admin  (/admin)
# =====================================================================
@admin_router.get("/orders", response_class=HTMLResponse)
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
        context={"store": store, "orders": orders, "board": BOARDS["admin"], "now": now()},
    )


@admin_router.get("/orders/feed", response_class=HTMLResponse)
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
        name="common/order_grid.html",
        context={"orders": orders, "board": BOARDS["admin"], "now": now()},
    )


@admin_router.post("/orders/{order_id}/advance", response_class=HTMLResponse)
async def advance_order(
    request: Request,
    order_id: int,
    target: OrderStatus = Form(...),
    role: Role = Form(Role.ADMIN),
    db: AsyncSession = Depends(get_db),
):
    """역할별 상태 변경 (공통 카드 버튼용).
    role 권한 검증 -> 전이 검증 -> 변경. 해당 역할 보드에서 사라질 상태면 카드 제거."""
    order, visible = await svc.change_status_by_role(db, order_id, target, role)
    return render_card(request, order, role.value) if visible else _empty()


# =====================================================================
# Kitchen  (/kitchen)
# =====================================================================
@kitchen_router.get("/board", response_class=HTMLResponse)
async def kitchen_board(request: Request, store_id: int = 1, db: AsyncSession = Depends(get_db)):
    store = await svc.get_store_or_404(db, store_id)
    orders = await svc.get_board_orders(db, store_id, Role.KITCHEN)
    return templates.TemplateResponse(
        request=request,
        name="kitchen/kds_board.html",
        context={"store": store, "orders": orders, "board": BOARDS["kitchen"], "now": now()},
    )


@kitchen_router.get("/feed", response_class=HTMLResponse)
async def kitchen_feed(request: Request, store_id: int = 1, db: AsyncSession = Depends(get_db)):
    orders = await svc.get_board_orders(db, store_id, Role.KITCHEN)
    return templates.TemplateResponse(
        request=request,
        name="common/order_grid.html",
        context={"orders": orders, "board": BOARDS["kitchen"], "now": now()},
    )


# =====================================================================
# Hall  (/hall)
# =====================================================================
@hall_router.get("/board", response_class=HTMLResponse)
async def hall_board(request: Request, store_id: int = 1, db: AsyncSession = Depends(get_db)):
    store = await svc.get_store_or_404(db, store_id)
    orders = await svc.get_board_orders(db, store_id, Role.HALL)
    return templates.TemplateResponse(
        request=request,
        name="hall/serving_board.html",
        context={"store": store, "orders": orders, "board": BOARDS["hall"], "now": now()},
    )


@hall_router.get("/feed", response_class=HTMLResponse)
async def hall_feed(request: Request, store_id: int = 1, db: AsyncSession = Depends(get_db)):
    orders = await svc.get_board_orders(db, store_id, Role.HALL)
    return templates.TemplateResponse(
        request=request,
        name="common/order_grid.html",
        context={"orders": orders, "board": BOARDS["hall"], "now": now()},
    )


# --- 하위 라우터 등록 ---
router.include_router(admin_router)
router.include_router(kitchen_router)
router.include_router(hall_router)
