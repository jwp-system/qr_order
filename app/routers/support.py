"""매장 관리자 사용 중 문의 (/admin/support) — 관리자 로그인 필수.

  GET  /admin/support    이 매장의 문의와 답변 목록 + 작성 칸 (열면 새 답변을 '확인함'으로 표시)
  POST /admin/support    문의 작성 (분류: 사용 중 문의 / 기능 요청 / 기타)

로그인한 매장으로 연결되므로 비밀번호가 없고, 공개 게시판(/inquiries)에는 나오지 않는다.
서비스 종료 후 조회 기간에도 쓸 수 있다 (core/security.py READONLY_PATHS / READONLY_POST_PATHS).
load_support_badge는 관리자 라우터들이 공통으로 걸어 헤더 '문의' 탭에 새 답변 수를 보여준다.
"""
import math
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import FailureLimiter
from app.core.security import require_admin
from app.core.templating import templates
from app.database.models import InquiryCategory
from app.database.session import get_db
from app.services import inquiry_service as svc
from app.services.store_service import get_store_or_404

Session = Dict[str, Any]

# 같은 매장에서 1시간에 10건까지
_post_limiter = FailureLimiter(max_failures=10, window_seconds=3600, lock_seconds=3600)


async def load_support_badge(
    request: Request, session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
) -> None:
    """관리자 화면 공통: 확인하지 않은 답변 수를 request.state에 (헤더 배지용)"""
    request.state.support_new = await svc.new_answer_count(db, session["store_id"])


router = APIRouter(prefix="/admin/support", tags=["Store Admin Support"], dependencies=[Depends(require_admin)])


async def _page(
    request: Request, db: AsyncSession, store_id: int, *,
    values: dict | None = None, error: str | None = None, created: bool = False, status_code: int = 200,
) -> HTMLResponse:
    store = await get_store_or_404(db, store_id)
    inquiries = await svc.store_inquiries(db, store_id)
    new_ids = {q.id for q in inquiries if q.has_new_answer}  # 표시용으로 먼저 계산한 뒤 '확인함' 처리
    if new_ids:
        await svc.mark_answers_seen(db, store_id)
    request.state.support_new = 0
    return templates.TemplateResponse(
        request=request,
        name="store_admin/support.html",
        context={
            "store": store,
            "inquiries": inquiries,
            "new_ids": new_ids,
            "categories": svc.STORE_CATEGORIES,
            "values": values or {"category": InquiryCategory.SUPPORT.value, **await svc.last_author(db, store_id)},
            "error": error,
            "created": created,
        },
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
async def support_page(
    request: Request, created: bool = False,
    session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    return await _page(request, db, session["store_id"], created=created)


@router.post("", response_class=HTMLResponse)
async def support_create(
    request: Request, session: Session = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    store_id = session["store_id"]
    form = await request.form()
    values = dict(form)
    key = str(store_id)
    wait = _post_limiter.retry_after(key)
    if wait:
        return await _page(request, db, store_id, values=values, status_code=429,
                           error=f"잠시 후 다시 시도해 주세요 ({math.ceil(wait / 60)}분).")
    try:
        await svc.create(db, form, store=await get_store_or_404(db, store_id))
    except svc.InquiryError as e:
        await db.rollback()
        return await _page(request, db, store_id, values=values, error=e.message, status_code=422)
    _post_limiter.record_failure(key)  # 실패가 아니라 작성 횟수로 센다
    return RedirectResponse(url="/admin/support?created=1", status_code=303)
