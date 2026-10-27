"""시스템(슈퍼) 관리자 로그인 / 로그아웃 (/super/login, /super/logout).

비밀번호는 환경변수 SUPER_ADMIN_PASSWORD. 이 라우터는 인증 없이 접근 가능해야 하므로
보호 대상인 super_admin.router와 분리되어 있다 (main.py 참고).
"""
import math

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import settings
from app.core.rate_limit import FailureLimiter
from app.core.security import create_super_session_token, verify_super_password
from app.core.templating import templates
from app.services.auth_service import safe_next_path

router = APIRouter(tags=["Super Admin Auth"])

HOME = "/super/dashboard"
COOKIE_PATH = "/super"  # 쿠키를 /super 경로에만 보낸다

# 같은 IP에서 10분 내 5번 실패 -> 15분 잠금 / 전체 합산 30번 실패 -> 15분 잠금 (분산 시도 방어)
_ip_limiter = FailureLimiter(max_failures=5, window_seconds=600, lock_seconds=900)
_global_limiter = FailureLimiter(max_failures=30, window_seconds=600, lock_seconds=900)


def _page(request: Request, *, next_url: str = "", error: str | None = None, status_code: int = 200):
    return templates.TemplateResponse(
        request=request,
        name="super_admin/login.html",
        context={"next": next_url, "error": error, "enabled": bool(settings.SUPER_ADMIN_PASSWORD)},
        status_code=status_code,
    )


def _next(value: str) -> str:
    # target = safe_next_path(value, prefix="/super/")
    # target = safe_next_path(f"/super/{value}")
    target = safe_next_path(f"{value}")
    print(target)
    return target if target and not target.startswith("/super/login") else ""


@router.get("/super/login", response_class=HTMLResponse)
async def super_login_page(request: Request, next_url: str = Query("", alias="next")):
    enabled = bool(settings.SUPER_ADMIN_PASSWORD)
    return _page(
        request,
        next_url=_next(next_url),
        error=None if enabled else "시스템 관리자 기능이 비활성화되어 있습니다. SUPER_ADMIN_PASSWORD를 설정하세요.",
        status_code=200 if enabled else 503,
    )


@router.post("/super/login", response_class=HTMLResponse)
async def super_login(
    request: Request,
    password: str = Form(...),
    next_url: str = Form("", alias="next"),
):
    safe_next = _next(next_url)
    if not settings.SUPER_ADMIN_PASSWORD:
        return _page(request, next_url=safe_next, error="시스템 관리자 기능이 비활성화되어 있습니다.", status_code=503)

    ip = request.client.host if request.client else "unknown"
    wait = max(_ip_limiter.retry_after(ip), _global_limiter.retry_after("all"))
    if wait:
        return _page(
            request, next_url=safe_next, status_code=429,
            error=f"시도 횟수를 초과했습니다. {math.ceil(wait / 60)}분 뒤에 다시 시도해 주세요.",
        )

    if not verify_super_password(password):
        _ip_limiter.record_failure(ip)
        _global_limiter.record_failure("all")
        return _page(request, next_url=safe_next, error="비밀번호가 올바르지 않습니다.", status_code=401)

    _ip_limiter.reset(ip)
    response = RedirectResponse(url=safe_next or HOME, status_code=303)
    response.set_cookie(
        key=settings.SUPER_SESSION_COOKIE_NAME,
        value=create_super_session_token(),
        max_age=settings.SUPER_SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="strict",  # 외부 사이트에서 오는 요청에는 쿠키를 보내지 않는다 (CSRF 방어)
        secure=settings.SESSION_COOKIE_SECURE,
        path=COOKIE_PATH,
    )
    return response


@router.post("/super/logout")
async def super_logout():
    """세션 쿠키 삭제 후 로그인 화면으로 (POST: 외부 링크로 강제 로그아웃되지 않게)"""
    response = RedirectResponse(url="/super/login", status_code=303)
    response.delete_cookie(
        key=settings.SUPER_SESSION_COOKIE_NAME,
        path=COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=settings.SESSION_COOKIE_SECURE,
    )
    return response