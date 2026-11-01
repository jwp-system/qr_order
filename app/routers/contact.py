"""회사 문의 (/contact) — 로그인 불필요. QR Order 문의 게시판(/inquiries)과 별개.

  GET  /contact          작성 화면 (?type=qr|academy|erp|custom|etc 로 종류 미리 선택, ?sent=1 이면 완료 안내)
  POST /contact          저장 후 CONTACT_EMAIL로 알림 메일 (services/contact_service.py, mail_service.py)
  GET  /contact/board    공개 목록 (종류·가린 이름·날짜·처리 상태만)

내용 확인·수정은 시스템 관리자 /super/contacts.
스팸 방지: 숨은 입력칸(website)에 값이 있으면 저장하지 않고 성공처럼 보이게 하고, IP별 작성 횟수를 제한한다.
"""
import math

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import FailureLimiter
from app.core.templating import templates
from app.core.urls import public_base_url
from app.database.session import get_db
from app.services import contact_service as svc
from app.services import mail_service

router = APIRouter(prefix="/contact", tags=["Contact"])

# 같은 IP에서 1시간에 5건까지 작성
_post_limiter = FailureLimiter(max_failures=5, window_seconds=3600, lock_seconds=3600)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _form_page(request: Request, values: dict, *, sent: bool = False, error: str | None = None, status_code: int = 200):
    return templates.TemplateResponse(
        request=request,
        name="contact/new.html",
        context={"values": values, "sent": sent, "error": error},
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
async def contact_form(request: Request, type: str | None = None, sent: bool = False):
    topic = type if type in svc.CONTACT_TOPICS else "custom"
    return _form_page(request, {"topic": topic}, sent=sent)


@router.post("", response_class=HTMLResponse)
async def contact_create(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    values = {k: v for k, v in form.items() if k != "website"}
    if form.get("website"):  # 사람에게는 보이지 않는 칸: 채워져 있으면 봇
        return RedirectResponse(url="/contact?sent=1", status_code=303)

    ip = _ip(request)
    wait = _post_limiter.retry_after(ip)
    if wait:
        return _form_page(request, values, error=f"잠시 후 다시 시도해 주세요 ({math.ceil(wait / 60)}분).", status_code=429)
    try:
        contact = await svc.create(db, form)
    except svc.ContactError as e:
        return _form_page(request, values, error=e.message, status_code=422)
    _post_limiter.record_failure(ip)  # 실패가 아니라 '작성 횟수'로 센다
    await mail_service.notify_contact(contact, f"{public_base_url(request)}/super/contacts?all=1#c-{contact.id}")
    return RedirectResponse(url="/contact?sent=1", status_code=303)


@router.get("/board", response_class=HTMLResponse)
async def contact_board(request: Request, page: int = 1, db: AsyncSession = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name="contact/board.html",
        context={"result": await svc.list_page(db, page)},
    )
