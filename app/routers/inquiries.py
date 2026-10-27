"""문의 게시판 (/inquiries) — 로그인 불필요.

  GET  /inquiries                  목록 (?type=분류, ?page=)
  GET  /inquiries/new              작성 화면 (?type=intro|trial|support|feature|etc 로 분류 미리 선택)
  POST /inquiries                  작성
  GET  /inquiries/{id}             공개글은 내용, 비밀글은 비밀번호 입력
  POST /inquiries/{id}             비밀번호 확인 후 비밀글 내용

연락처는 어느 화면에도 내보내지 않는다 (시스템 관리자 /super/inquiries 에서만).
매장 관리자 화면에서 남긴 문의(store_id 있음)는 이 게시판에 나오지 않는다 (/admin/support).
스팸 방지: 숨은 입력칸(website)에 값이 있으면 저장하지 않고 성공처럼 보이게 하고, IP별 작성 횟수를 제한한다.
"""
import math

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import FailureLimiter
from app.core.templating import templates
from app.database.models import InquiryCategory
from app.database.session import get_db
from app.domain.inquiry import CATEGORY_BY_QUERY
from app.services import inquiry_service as svc

router = APIRouter(prefix="/inquiries", tags=["Inquiries"])

# 같은 IP에서 1시간에 5건까지 작성 / 비밀번호 10분 내 10번 틀리면 10분 잠금
_post_limiter = FailureLimiter(max_failures=5, window_seconds=3600, lock_seconds=3600)
_password_limiter = FailureLimiter(max_failures=10, window_seconds=600, lock_seconds=600)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _category(query: str | None) -> InquiryCategory | None:
    return CATEGORY_BY_QUERY.get((query or "").strip().lower())


@router.get("", response_class=HTMLResponse)
async def inquiry_list(
    request: Request, type: str | None = None, page: int = 1, created: bool = False,
    db: AsyncSession = Depends(get_db),
):
    category = _category(type)
    result = await svc.list_page(db, page, category, public=True)
    return templates.TemplateResponse(
        request=request,
        name="inquiries/list.html",
        context={"result": result, "category": category, "created": created},
    )


def _form_page(request: Request, values: dict, *, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="inquiries/new.html",
        context={"values": values, "error": error},
        status_code=status_code,
    )


@router.get("/new", response_class=HTMLResponse)
async def inquiry_new(request: Request, type: str | None = None):
    category = _category(type) or InquiryCategory.INTRO
    return _form_page(request, {"category": category.value, "is_private": "on"})


@router.post("", response_class=HTMLResponse)
async def inquiry_create(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    values = {k: v for k, v in form.items() if k not in ("password", "website")}
    if form.get("website"):  # 사람에게는 보이지 않는 칸: 채워져 있으면 봇
        return RedirectResponse(url="/inquiries?created=1", status_code=303)

    ip = _ip(request)
    wait = _post_limiter.retry_after(ip)
    if wait:
        return _form_page(request, values, error=f"잠시 후 다시 시도해 주세요 ({math.ceil(wait / 60)}분).", status_code=429)
    try:
        await svc.create(db, form)
    except svc.InquiryError as e:
        await db.rollback()
        return _form_page(request, values, error=e.message, status_code=422)
    _post_limiter.record_failure(ip)  # 실패가 아니라 '작성 횟수'로 센다
    return RedirectResponse(url="/inquiries?created=1", status_code=303)


def _detail(request: Request, inquiry, *, unlocked: bool, error: str | None = None, status_code: int = 200):
    return templates.TemplateResponse(
        request=request,
        name="inquiries/detail.html",
        context={"inquiry": inquiry, "unlocked": unlocked, "error": error},
        status_code=status_code,
    )


@router.get("/{inquiry_id}", response_class=HTMLResponse)
async def inquiry_detail(request: Request, inquiry_id: int, db: AsyncSession = Depends(get_db)):
    inquiry = await svc.get(db, inquiry_id, public=True)
    return _detail(request, inquiry, unlocked=svc.can_view(inquiry, None))


@router.post("/{inquiry_id}", response_class=HTMLResponse)
async def inquiry_unlock(
    request: Request, inquiry_id: int, password: str = Form(""), db: AsyncSession = Depends(get_db),
):
    inquiry = await svc.get(db, inquiry_id, public=True)
    key = f"{_ip(request)}:{inquiry_id}"
    wait = _password_limiter.retry_after(key)
    if wait:
        return _detail(request, inquiry, unlocked=False, status_code=429,
                       error=f"비밀번호를 여러 번 틀렸습니다. {math.ceil(wait / 60)}분 뒤에 다시 시도해 주세요.")
    if not svc.can_view(inquiry, password):
        _password_limiter.record_failure(key)
        return _detail(request, inquiry, unlocked=False, error="비밀번호가 맞지 않습니다.", status_code=403)
    _password_limiter.reset(key)
    return _detail(request, inquiry, unlocked=True)

