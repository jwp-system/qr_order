"""식당용 API (관리자 / 주방 KDS / 홀 서빙) — 모두 스태프 로그인 필수.

  /admin/...                    관리자 전용 (주문 현황, 테이블 정산 /admin/settlement)
  /kitchen/...                  주방 (+관리자)
  /hall/...                     홀 (+관리자)
  /staff/orders/{id}/advance    공통 카드 버튼이 호출하는 상태 변경 (관리자/주방/홀)

매장은 요청 값이 아니라 로그인 세션의 store_id로 정한다 (다른 매장 데이터는 볼 수도 바꿀 수도 없다).
라우터 단위로 의존성을 걸어 두었으므로, 이 파일에 라우트를 추가해도 인증이 빠지지 않는다.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.board_config import BOARDS
from app.core.security import require_admin, require_hall, require_kitchen, require_staff
from app.core.templating import now, render_card, templates
from app.database.models import OrderStatus
from app.database.session import get_db
from app.domain.roles import Role
from app.services import settlement_service
from app.services import store_service as svc

Session = Dict[str, Any]

admin_router = APIRouter(prefix="/admin", tags=["Store Admin"], dependencies=[Depends(require_admin)])
kitchen_router = APIRouter(prefix="/kitchen", tags=["Kitchen KDS"], dependencies=[Depends(require_kitchen)])
hall_router = APIRouter(prefix="/hall", tags=["Hall Serving"], dependencies=[Depends(require_hall)])
staff_router = APIRouter(prefix="/staff", tags=["Staff"], dependencies=[Depends(require_staff)])

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
    session: Session = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    store = await svc.get_store_or_404(db, session["store_id"])
    orders = await svc.get_orders(db, store.id)
    return templates.TemplateResponse(
        request=request,
        name="store_admin/order_board.html",
        context={"store": store, "orders": orders, "board": BOARDS["admin"], "now": now()},
    )


@admin_router.get("/orders/feed", response_class=HTMLResponse)
async def admin_orders_feed(
    request: Request,
    order_status: Optional[OrderStatus] = None,  # ?order_status=COOKING
    session: Session = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    statuses = [order_status] if order_status else svc.ACTIVE_STATUSES
    orders = await svc.get_orders(db, session["store_id"], statuses)
    return templates.TemplateResponse(
        request=request,
        name="common/order_grid.html",
        context={"orders": orders, "board": BOARDS["admin"], "now": now()},
    )


# --- 테이블 정산 / 일괄 마감 ---
async def _settlement_context(db: AsyncSession, store_id: int) -> dict:
    return {
        "tables": await settlement_service.get_table_summaries(db, store_id),
        "today": await settlement_service.get_today_summary(db, store_id),
        "now": now(),
    }


@admin_router.get("/settlement", response_class=HTMLResponse)
async def admin_settlement_page(
    request: Request,
    session: Session = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    store = await svc.get_store_or_404(db, session["store_id"])
    return templates.TemplateResponse(
        request=request,
        name="store_admin/settlement.html",
        context={"store": store, **await _settlement_context(db, store.id)},
    )


@admin_router.get("/settlement/feed", response_class=HTMLResponse)
async def admin_settlement_feed(
    request: Request,
    session: Session = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return templates.TemplateResponse(
        request=request,
        name="store_admin/fragments/table_grid.html",
        context=await _settlement_context(db, session["store_id"]),
    )


@admin_router.post("/settlement/tables/{table_id}/close", response_class=HTMLResponse)
async def admin_close_table(
    request: Request,
    table_id: int,
    order_ids: str = Form(""),  # 화면에서 확인한 주문 id (쉼표 구분)
    session: Session = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """테이블 정산 완료: 진행 중 주문을 모두 COMPLETED로 일괄 마감.
    결과 메시지는 #settle-flash에 out-of-band로 넣는다 (SSE 갱신으로 목록이 다시 그려져도 남도록)."""
    store_id = session["store_id"]
    try:
        expected = {int(x) for x in order_ids.split(",") if x.strip()}
    except ValueError:
        expected = set()
    try:
        table, count, amount = await settlement_service.close_table(db, store_id, table_id, expected)
        flash = {"ok": True, "text": f"{table.table_number}번 테이블 {count}건, {amount:,}원 정산을 완료했습니다."}
    except settlement_service.SettlementError as e:
        flash = {"ok": False, "text": e.message}
    return templates.TemplateResponse(
        request=request,
        name="store_admin/fragments/table_grid.html",
        context={**await _settlement_context(db, store_id), "flash": flash},
    )


# =====================================================================
# Kitchen  (/kitchen)
# =====================================================================
@kitchen_router.get("/board", response_class=HTMLResponse)
async def kitchen_board(
    request: Request,
    session: Session = Depends(require_kitchen),
    db: AsyncSession = Depends(get_db),
):
    store = await svc.get_store_or_404(db, session["store_id"])
    orders = await svc.get_board_orders(db, store.id, Role.KITCHEN)
    return templates.TemplateResponse(
        request=request,
        name="kitchen/kds_board.html",
        context={"store": store, "orders": orders, "board": BOARDS["kitchen"], "now": now()},
    )


@kitchen_router.get("/feed", response_class=HTMLResponse)
async def kitchen_feed(
    request: Request,
    session: Session = Depends(require_kitchen),
    db: AsyncSession = Depends(get_db),
):
    orders = await svc.get_board_orders(db, session["store_id"], Role.KITCHEN)
    return templates.TemplateResponse(
        request=request,
        name="common/order_grid.html",
        context={"orders": orders, "board": BOARDS["kitchen"], "now": now()},
    )


# =====================================================================
# Hall  (/hall)
# =====================================================================
@hall_router.get("/board", response_class=HTMLResponse)
async def hall_board(
    request: Request,
    session: Session = Depends(require_hall),
    db: AsyncSession = Depends(get_db),
):
    store = await svc.get_store_or_404(db, session["store_id"])
    orders = await svc.get_board_orders(db, store.id, Role.HALL)
    return templates.TemplateResponse(
        request=request,
        name="hall/serving_board.html",
        context={"store": store, "orders": orders, "board": BOARDS["hall"], "now": now()},
    )


@hall_router.get("/feed", response_class=HTMLResponse)
async def hall_feed(
    request: Request,
    session: Session = Depends(require_hall),
    db: AsyncSession = Depends(get_db),
):
    orders = await svc.get_board_orders(db, session["store_id"], Role.HALL)
    return templates.TemplateResponse(
        request=request,
        name="common/order_grid.html",
        context={"orders": orders, "board": BOARDS["hall"], "now": now()},
    )


# =====================================================================
# Staff 공통  (/staff)
# =====================================================================
@staff_router.post("/orders/{order_id}/advance", response_class=HTMLResponse)
async def advance_order(
    request: Request,
    order_id: int,
    target: OrderStatus = Form(...),
    role: Optional[Role] = Form(None),  # 카드를 누른 보드. 없으면 로그인한 역할의 보드
    session: Session = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
):
    """공통 카드 버튼용 상태 변경.
    권한은 폼의 role이 아니라 로그인 세션의 역할로 판단한다 (폼 값은 어느 보드의 카드를 돌려줄지만 정한다).
    역할 권한 + 보드 사용 권한 + 매장 소속 + 전이 규칙을 서버에서 모두 검증하고,
    해당 보드에서 사라질 상태면 카드를 제거한다."""
    actor = Role(session["role"])
    board = role or actor
    order, visible = await svc.change_status_by_role(
        db, order_id, target, actor=actor, board=board, store_id=session["store_id"]
    )
    return render_card(request, order, board.value) if visible else _empty()


# --- 하위 라우터 등록 ---
router.include_router(admin_router)
router.include_router(kitchen_router)
router.include_router(hall_router)
router.include_router(staff_router)