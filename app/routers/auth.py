"""스태프 로그인 / 로그아웃 (매장 코드 + 역할 + PIN).

로그인에 성공하면 core/security.py의 세션 JWT를 쿠키로 내려주고 역할별 첫 화면으로 보낸다.
관리자가 기본 PIN으로 로그인했으면 PIN 변경 화면(/admin/pin)으로 보내고, 바꾸기 전까지 다른 화면은 막는다.
"""
import math
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rate_limit import FailureLimiter
from app.core.clock import business_today
from app.core.security import (
    PIN_CHANGE_PATH,
    READONLY_HOME,
    create_staff_session_token,
    get_current_staff_session,
    require_admin_pin_setup,
)
from app.core.templating import templates
from app.database.session import get_db
from app.domain.license import StoreAccess, readonly_until
from app.domain.roles import Role
from app.services import auth_service as svc
from app.services.store_service import get_store_or_404

router = APIRouter(tags=["Auth"])

ROLE_LABELS = (
    (Role.ADMIN.value, "관리자"),
    (Role.KITCHEN.value, "주방"),
    (Role.HALL.value, "홀"),
)
# 역할별 로그인 후 첫 화면 (매장은 세션으로 정해지므로 쿼리 없이)
ROLE_HOME = {
    Role.ADMIN: "/admin/board",
    Role.KITCHEN: "/kitchen/board",
    Role.HALL: "/hall/board",
}

# 같은 IP+매장에서 10분 내 5번 실패 -> 5분 잠금 / 같은 매장+역할에서 10분 내 20번 실패 -> 5분 잠금
_ip_limiter = FailureLimiter(max_failures=5, window_seconds=600, lock_seconds=300)
_store_limiter = FailureLimiter(max_failures=20, window_seconds=600, lock_seconds=300)
# PIN 변경 화면의 현재 PIN 확인: 같은 매장에서 10분 내 5번 실패 -> 5분 잠금
_pin_change_limiter = FailureLimiter(max_failures=5, window_seconds=600, lock_seconds=300)


def _set_session_cookie(response: RedirectResponse, token: str) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.SESSION_MAX_AGE_SECONDS,
        httponly=settings.SESSION_COOKIE_HTTPONLY,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        secure=settings.SESSION_COOKIE_SECURE,
        path="/",
    )


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
        store, access = await svc.authenticate_staff(db, code, role, pin)
    except svc.StaffAuthError as e:
        if e.counts_as_failure:
            _ip_limiter.record_failure(ip_key)
            _store_limiter.record_failure(store_key)
        return page(e.message, e.status_code)

    _ip_limiter.reset(ip_key)
    _store_limiter.reset(store_key)

    # --- 세션 쿠키 발급 후 이동 (POST -> GET 이므로 303) ---
    if access is StoreAccess.READONLY:
        # 서비스 종료 매장: 조회 전용 세션으로 주문 내역부터 (PIN 변경 강제 없음)
        response = RedirectResponse(url=READONLY_HOME, status_code=303)
        until = readonly_until(store.expired_on or business_today())
        _set_session_cookie(response, create_staff_session_token(store.id, role, readonly_until=until))
        return response

    # 관리자가 기본 PIN(관리자/주방/홀 중 하나라도)을 쓰고 있으면 PIN 변경부터 하게 한다
    pin_change = role is Role.ADMIN and bool(svc.default_pin_roles(store))
    if pin_change:
        target = PIN_CHANGE_PATH + (f"?next={quote(safe_next, safe='/')}" if safe_next else "")
    else:
        target = safe_next or ROLE_HOME[role]
    response = RedirectResponse(url=target, status_code=303)
    _set_session_cookie(response, create_staff_session_token(store.id, role, pin_change_required=pin_change))
    return response


# =====================================================================
# PIN 변경 (관리자 전용, 기본 PIN 상태에서도 접근 가능)
# =====================================================================
def _pin_page(
    request: Request,
    store,
    *,
    next_url: str = "",
    error: str | None = None,
    message: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="store_admin/pin_settings.html",
        context={
            "store": store,
            "roles": ROLE_LABELS,
            "default_roles": [r.value for r in svc.default_pin_roles(store)],
            "next": next_url,
            "error": error,
            "message": message,
        },
        status_code=status_code,
    )


@router.get(PIN_CHANGE_PATH, response_class=HTMLResponse)
async def pin_settings_page(
    request: Request,
    next_url: str = Query("", alias="next"),
    changed: bool = False,
    session: dict = Depends(require_admin_pin_setup),
    db: AsyncSession = Depends(get_db),
):
    store = await get_store_or_404(db, session["store_id"])
    return _pin_page(
        request, store,
        next_url=svc.safe_next_path(next_url) or "",
        message="PIN이 변경되었습니다." if changed else None,
    )


@router.post(PIN_CHANGE_PATH, response_class=HTMLResponse)
async def change_pins(
    request: Request,
    current_pin: str = Form(...),
    admin_pin: str = Form(""),
    admin_pin_confirm: str = Form(""),
    kitchen_pin: str = Form(""),
    hall_pin: str = Form(""),
    next_url: str = Form("", alias="next"),
    session: dict = Depends(require_admin_pin_setup),
    db: AsyncSession = Depends(get_db),
):
    store = await get_store_or_404(db, session["store_id"])
    safe_next = svc.safe_next_path(next_url) or ""

    def page(error: str, status_code: int) -> HTMLResponse:
        return _pin_page(request, store, next_url=safe_next, error=error, status_code=status_code)

    limit_key = str(store.id)
    wait = _pin_change_limiter.retry_after(limit_key)
    if wait:
        return page(f"시도 횟수를 초과했습니다. {math.ceil(wait / 60)}분 뒤에 다시 시도해 주세요.", 429)
    if admin_pin != admin_pin_confirm:
        return page("새 관리자 PIN 확인이 일치하지 않습니다.", 400)

    try:
        await svc.change_pins(
            db, store, current_pin,
            {Role.ADMIN: admin_pin, Role.KITCHEN: kitchen_pin, Role.HALL: hall_pin},
        )
    except svc.PinChangeError as e:
        if e.counts_as_failure:
            _pin_change_limiter.record_failure(limit_key)
        return page(e.message, 400)
    _pin_change_limiter.reset(limit_key)

    # 변경 강제 해제된 세션으로 다시 발급. 강제 변경이었다면 원래 가려던 곳(또는 관리자 첫 화면)으로 보낸다
    if session.get("pin_change"):
        target = safe_next or ROLE_HOME[Role.ADMIN]
    else:
        target = f"{PIN_CHANGE_PATH}?changed=1"
    response = RedirectResponse(url=target, status_code=303)
    _set_session_cookie(response, create_staff_session_token(store.id, Role.ADMIN))
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
