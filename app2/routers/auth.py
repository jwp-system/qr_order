"""스태프 로그인 / 로그아웃 (매장 코드 + 역할 + PIN).

로그인에 성공하면 core/security.py의 세션 JWT를 쿠키로 내려주고 역할별 첫 화면으로 보낸다.
"""
import math

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app2.core.config import settings
from app2.core.rate_limit import FailureLimiter
from app2.core.security import create_staff_session_token, get_current_staff_session
from app2.core.templating import templates
from app2.database.session import get_db
from app2.domain.roles import Role
from app2.services import auth_service as svc

router = APIRouter(tags=["Auth"])

ROLE_LABELS = (
    (Role.ADMIN.value, "관리자"),
    (Role.KITCHEN.value, "주방"),
    (Role.HALL.value, "홀"),
)
# 역할별 로그인 후 첫 화면. store_id 쿼리는 보드가 인증 연동 전까지 쿼리로 매장을 구분하기 때문.
ROLE_HOME = {
    Role.ADMIN: "/admin/orders",
    Role.KITCHEN: "/kitchen/board",
    Role.HALL: "/hall/board",
}

# 같은 IP+매장에서 10분 내 5번 실패 -> 5분 잠금 / 같은 매장+역할에서 10분 내 20번 실패 -> 5분 잠금
_ip_limiter = FailureLimiter(max_failures=5, window_seconds=600, lock_seconds=300)
_store_limiter = FailureLimiter(max_failures=20, window_seconds=600, lock_seconds=300)


def _login_page(
    request: Request,
    *,
    role: Role = Role.ADMIN,
    service_code: str = "",
    next_url: str = "",
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={
            "roles": ROLE_LABELS,
            "role": role.value,
            "service_code": service_code,
            "next": next_url,
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    role: str = "admin",
    code: str = "",
    next_url: str = Query("", alias="next"),
):
    """로그인 화면. 주방/홀 태블릿은 /login?role=kitchen 처럼 북마크해 두면 역할이 미리 선택된다."""
    try:
        selected = Role(role)
    except ValueError:
        selected = Role.ADMIN
    return _login_page(
        request,
        role=selected,
        service_code=svc.normalize_service_code(code),
        next_url=svc.safe_next_path(next_url) or "",
    )


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    service_code: str = Form(...),
    role: Role = Form(...),
    pin: str = Form(...),
    next_url: str = Form("", alias="next"),
    db: AsyncSession = Depends(get_db),
):
    code = svc.normalize_service_code(service_code)
    safe_next = svc.safe_next_path(next_url) or ""

    def page(error: str, status_code: int) -> HTMLResponse:
        return _login_page(
            request, role=role, service_code=code, next_url=safe_next,
            error=error, status_code=status_code,
        )

    # --- 실패 횟수 제한 ---
    client_ip = request.client.host if request.client else "unknown"
    ip_key = f"{client_ip}:{code}"
    store_key = f"{code}:{role.value}"
    wait = max(_ip_limiter.retry_after(ip_key), _store_limiter.retry_after(store_key))
    if wait:
        return page(f"시도 횟수를 초과했습니다. {math.ceil(wait / 60)}분 뒤에 다시 시도해 주세요.", 429)

    if not svc.is_valid_pin(pin):
        return page(f"PIN은 숫자 {svc.PIN_LENGTH}자리입니다.", 400)

    # --- 인증 ---
    try:
        store = await svc.authenticate_staff(db, code, role, pin)
    except svc.StaffAuthError as e:
        if e.counts_as_failure:
            _ip_limiter.record_failure(ip_key)
            _store_limiter.record_failure(store_key)
        return page(e.message, e.status_code)

    _ip_limiter.reset(ip_key)
    _store_limiter.reset(store_key)

    # --- 세션 쿠키 발급 후 이동 (POST -> GET 이므로 303) ---
    target = safe_next or f"{ROLE_HOME[role]}?store_id={store.id}"
    response = RedirectResponse(url=target, status_code=303)
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=create_staff_session_token(store.id, role),
        max_age=settings.SESSION_MAX_AGE_SECONDS,
        httponly=settings.SESSION_COOKIE_HTTPONLY,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        secure=settings.SESSION_COOKIE_SECURE,
        path="/",
    )
    return response


@router.post("/logout")
async def logout(request: Request):
    """세션 쿠키 삭제 후 같은 역할의 로그인 화면으로. (GET이 아니라 POST: 외부 링크로 강제 로그아웃되지 않게)"""
    role = Role.ADMIN.value
    try:
        role = get_current_staff_session(request).get("role", role)
    except HTTPException:
        pass  # 이미 만료/없음: 그대로 로그인 화면으로

    response = RedirectResponse(url=f"/login?role={role}", status_code=303)
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
        httponly=settings.SESSION_COOKIE_HTTPONLY,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        secure=settings.SESSION_COOKIE_SECURE,
    )
    return response
