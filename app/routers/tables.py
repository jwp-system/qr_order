"""매장 관리자 테이블 관리 (/admin/tables) — 관리자 로그인 필수.

  GET  /admin/tables                       테이블 관리 화면
  GET  /admin/tables/body                  테이블 목록 프래그먼트
  POST /admin/tables                       테이블 추가 (시작 번호 + 개수)
  POST /admin/tables/{id}                  테이블 번호 수정
  POST /admin/tables/{id}/status           상태 변경 (주문 가능 / 주문 중지 / 사용 안 함)
  POST /admin/tables/{id}/delete           삭제 (주문 이력이 없을 때만)
  GET  /admin/tables/{id}/qr               QR 모달
  GET  /admin/tables/{id}/qr.png           QR 이미지 (?download=1 이면 파일로 저장)
  POST /admin/tables/{id}/qr/reissue       QR 재발급 (기존 QR 무효화)
  GET  /admin/tables/qr-print              전체 QR 인쇄용 페이지

목록을 바꾸는 요청은 목록 전체(#tables-body)를 다시 그려 돌려준다. 실패는 422 + 오류 문구.
매장은 요청 값이 아니라 로그인 세션의 store_id로 정한다.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_admin
from app.core.templating import templates
from app.core.urls import is_local, order_url, public_base_url
from app.database.models import Table, TableStatus
from app.database.session import get_db
from app.routers.support import load_support_badge
from app.services import table_service as svc
from app.services.store_service import get_store_or_404

Session = Dict[str, Any]

router = APIRouter(
    prefix="/admin/tables", tags=["Store Admin Tables"],
    dependencies=[Depends(require_admin), Depends(load_support_badge)],  # 헤더 '문의' 새 답변 배지
)

UNPROCESSABLE = 422


async def _body(
    request: Request, db: AsyncSession, store_id: int, *,
    error: str | None = None, message: str | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="store_admin/fragments/table_manage_body.html",
        context={
            "rows": await svc.list_tables(db, store_id),
            "statuses": list(TableStatus),
            "error": error,
            "message": message,
        },
        status_code=UNPROCESSABLE if error else 200,
    )


async def _run(request: Request, db: AsyncSession, store_id: int, action, message=None) -> HTMLResponse:
    """변경 공통 처리: 실행 -> 실패 시 롤백 후 오류 표시 -> 목록 다시 그리기"""
    try:
        result = await action()
    except svc.TableError as e:
        await db.rollback()
        return await _body(request, db, store_id, error=e.message)
    return await _body(request, db, store_id, message=message(result) if callable(message) else message)


# =====================================================================
# 화면 / 목록
# =====================================================================
@router.get("", response_class=HTMLResponse)
async def tables_page(request: Request, session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    store = await get_store_or_404(db, session["store_id"])
    return templates.TemplateResponse(
        request=request,
        name="store_admin/table_manage.html",
        context={
            "store": store,
            "rows": await svc.list_tables(db, store.id),
            "statuses": list(TableStatus),
            "qr_base_url": public_base_url(request),
            "qr_is_local": is_local(request),
            "error": None,
            "message": None,
        },
    )


@router.get("/body", response_class=HTMLResponse)
async def tables_body(request: Request, session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await _body(request, db, session["store_id"])


@router.get("/qr-print", response_class=HTMLResponse)
async def qr_print_page(request: Request, session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """'사용 안 함'을 뺀 모든 테이블의 QR을 한 장에 모아 인쇄"""
    store = await get_store_or_404(db, session["store_id"])
    rows = [r for r in await svc.list_tables(db, store.id) if r.table.status is not TableStatus.DISABLED]
    return templates.TemplateResponse(
        request=request,
        name="store_admin/table_qr_print.html",
        context={"store": store, "tables": [r.table for r in rows], "qr_is_local": is_local(request)},
    )


# =====================================================================
# 추가 / 수정 / 상태 / 삭제
# =====================================================================
@router.post("", response_class=HTMLResponse)
async def add_tables(request: Request, session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    store_id = session["store_id"]
    form = await request.form()

    def added(numbers: list[int]) -> str:
        span = f"{numbers[0]}번" if len(numbers) == 1 else f"{numbers[0]}~{numbers[-1]}번"
        return f"테이블 {span}을(를) 추가했습니다. QR을 인쇄해 테이블에 붙여 주세요."

    return await _run(request, db, store_id, lambda: svc.add_tables(db, store_id, form), message=added)


@router.post("/{table_id}", response_class=HTMLResponse)
async def update_table(
    request: Request, table_id: int,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    store_id = session["store_id"]
    table = await svc.get_table(db, store_id, table_id)
    form = await request.form()
    return await _run(request, db, store_id, lambda: svc.update_number(db, table, form))


@router.post("/{table_id}/status", response_class=HTMLResponse)
async def update_table_status(
    request: Request, table_id: int, status: TableStatus = Form(...),
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    store_id = session["store_id"]
    table = await svc.get_table(db, store_id, table_id)
    return await _run(request, db, store_id, lambda: svc.set_status(db, table, status))


@router.post("/{table_id}/delete", response_class=HTMLResponse)
async def delete_table(
    request: Request, table_id: int,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    store_id = session["store_id"]
    table = await svc.get_table(db, store_id, table_id)
    number = table.table_number
    return await _run(
        request, db, store_id, lambda: svc.delete_table(db, table), message=f"{number}번 테이블을 삭제했습니다.",
    )


# =====================================================================
# QR
# =====================================================================
def _qr_modal(request: Request, table: Table, message: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="store_admin/fragments/table_qr_modal.html",
        context={
            "table": table,
            "order_url": order_url(request, table.qr_token),
            "qr_is_local": is_local(request),
            "message": message,
        },
    )


@router.get("/{table_id}/qr", response_class=HTMLResponse)
async def qr_modal(
    request: Request, table_id: int,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    return _qr_modal(request, await svc.get_table(db, session["store_id"], table_id))


@router.get("/{table_id}/qr.png")
async def qr_image(
    request: Request, table_id: int, download: bool = False,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    table = await svc.get_table(db, session["store_id"], table_id)
    headers = {"Cache-Control": "no-store"}  # 재발급 후 이전 QR 이미지가 캐시에서 보이지 않도록
    if download:
        headers["Content-Disposition"] = f'attachment; filename="table-{table.table_number}-qr.png"'
    return Response(content=svc.qr_png(order_url(request, table.qr_token)), media_type="image/png", headers=headers)


@router.post("/{table_id}/qr/reissue", response_class=HTMLResponse)
async def reissue_qr(
    request: Request, table_id: int,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    table = await svc.get_table(db, session["store_id"], table_id)
    await svc.reissue_qr(db, table)
    return _qr_modal(request, table, message="QR을 새로 발급했습니다. 기존에 붙인 QR은 더 이상 열리지 않으니 새로 인쇄해 주세요.")
