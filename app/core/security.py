# app/core/security.py
import hashlib
import hmac
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict
from urllib.parse import quote, urlsplit

import jwt
from fastapi import HTTPException, Request, status

from app.core.clock import business_today
from app.core.config import settings
from app.domain.roles import Role  # security.Role 로도 import 가능 (하위 호환)


_STAFF_ROLES = frozenset(r.value for r in Role)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


# =====================================================================
# 1. PIN 코드 해싱 및 검증
# =====================================================================
def hash_pin(pin: str) -> str:
    """4자리 PIN을 SECRET_KEY로 솔트하여 SHA-256 해싱"""
    salted = f"{pin}:{settings.SECRET_KEY}"
    return hashlib.sha256(salted.encode("utf-8")).hexdigest()


def verify_pin(plain_pin: str, hashed_pin: str) -> bool:
    return hmac.compare_digest(hash_pin(plain_pin), hashed_pin)  # 타이밍 공격 방지


# 문의 게시판 비밀번호: 사람마다 다른 소금값 + PBKDF2 ("pbkdf2$반복횟수$salt$hash")
_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, expected = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), expected)


# =====================================================================
# 2. 매장 스태프 세션 쿠키
# =====================================================================
PIN_CHANGE_PATH = "/admin/pin"  # 기본 PIN 변경 화면 (변경 전에는 이 화면만 쓸 수 있다)

# 서비스 종료 후 조회 전용 세션이 쓸 수 있는 화면 (GET만). 그 외 화면 이동은 주문 내역으로 보낸다.
READONLY_HOME = "/admin/history"
READONLY_PATHS = frozenset({READONLY_HOME, "/admin/history.csv", "/admin/menu", "/admin/menu/body", "/admin/support"})
READONLY_POST_PATHS = frozenset({"/admin/support"})  # 종료 후에도 문의는 남길 수 있다


def create_staff_session_token(
    store_id: int, role: Role, *, pin_change_required: bool = False, readonly_until: date | None = None,
) -> str:
    """관리자/주방/홀 PIN 인증 성공 시 세션 JWT 생성.
    pin_change_required: 기본 PIN으로 로그인한 관리자. PIN을 바꾸기 전까지 다른 화면 접근을 막는다.
    readonly_until: 서비스 종료 매장의 조회 전용 세션. 이 날짜(영업일)까지 READONLY_PATHS만 볼 수 있다."""
    expire = datetime.now(timezone.utc) + timedelta(seconds=settings.SESSION_MAX_AGE_SECONDS)
    payload = {"store_id": store_id, "role": role.value, "exp": expire}
    if pin_change_required:
        payload["pin_change"] = True
    if readonly_until:
        payload["ro_until"] = readonly_until.isoformat()
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def get_current_staff_session(request: Request) -> Dict[str, Any]:
    """쿠키에서 세션을 추출하고 파싱"""
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not token:
        raise _unauthorized("로그인이 필요한 페이지입니다.")
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise _unauthorized("세션이 만료되었습니다.") from None
    except jwt.PyJWTError:
        raise _unauthorized("유효하지 않은 세션입니다.") from None
    # 같은 키로 서명된 다른 종류의 토큰(슈퍼 관리자 등)은 스태프 세션으로 인정하지 않는다
    if payload.get("role") not in _STAFF_ROLES or not isinstance(payload.get("store_id"), int):
        raise _unauthorized("유효하지 않은 세션입니다.")
    return payload


# =====================================================================
# 3. 권한 인가 의존성 (RBAC)
# =====================================================================
def _hint_role(request: Request) -> str | None:
    """만료/무효 세션에서 이전 역할만 읽는다 (서명 검증 없음). 로그인 화면의 역할 미리 선택에만 쓴다."""
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        claims = jwt.decode(
            token, options={"verify_signature": False, "verify_exp": False},
            algorithms=[settings.ALGORITHM],
        )
    except jwt.PyJWTError:
        return None
    role = claims.get("role")
    return role if role in _STAFF_ROLES else None


def _staff_denied(
    request: Request, *, login_role: Role, allowed: frozenset[str], status_code: int, detail: str
) -> HTTPException:
    """미로그인(401)/권한 없음(403) 응답. 요청 종류별로 다르게 돌려준다.
      - 화면 이동(GET)      -> 303 /login?role=..&next=<현재 경로>
      - HTMX 요청           -> HX-Redirect (현재 페이지를 next로)
      - SSE/그 외 API 요청  -> 그대로 401/403 (EventSource는 리다이렉트를 따라가지 못함)"""
    hint = _hint_role(request) if status_code == status.HTTP_401_UNAUTHORIZED else None
    role = hint if hint and (hint == Role.ADMIN.value or hint in allowed) else login_role.value

    if request.headers.get("HX-Request"):
        current = urlsplit(request.headers.get("HX-Current-URL", "")).path
        location = f"/login?role={role}" + (f"&next={quote(current, safe='/')}" if current else "")
        return HTTPException(status_code=status_code, detail=detail, headers={"HX-Redirect": location})
    if request.method == "GET" and "text/event-stream" not in request.headers.get("accept", ""):
        location = f"/login?role={role}&next={quote(request.url.path, safe='/')}"
        return HTTPException(
            status_code=status.HTTP_303_SEE_OTHER, detail=detail, headers={"Location": location}
        )
    return HTTPException(status_code=status_code, detail=detail)


def _pin_change_required(request: Request) -> HTTPException:
    """기본 PIN 변경 전 세션: 화면 이동은 PIN 변경 화면으로, HTMX는 HX-Redirect, 그 외는 403"""
    detail = "기본 PIN을 변경해야 합니다."
    if request.headers.get("HX-Request"):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=detail, headers={"HX-Redirect": PIN_CHANGE_PATH}
        )
    if request.method == "GET" and "text/event-stream" not in request.headers.get("accept", ""):
        location = f"{PIN_CHANGE_PATH}?next={quote(request.url.path, safe='/')}"
        return HTTPException(status_code=status.HTTP_303_SEE_OTHER, detail=detail, headers={"Location": location})
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _readonly_denied(request: Request) -> HTTPException:
    """조회 전용 세션의 허용되지 않은 요청: 화면 이동은 주문 내역으로, HTMX는 HX-Redirect, 그 외는 403"""
    detail = "서비스가 종료되어 조회만 할 수 있습니다."
    if request.headers.get("HX-Request"):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail, headers={"HX-Redirect": READONLY_HOME})
    if request.method == "GET" and "text/event-stream" not in request.headers.get("accept", ""):
        return HTTPException(status_code=status.HTTP_303_SEE_OTHER, detail=detail, headers={"Location": READONLY_HOME})
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def readonly_until_of(session: Dict[str, Any]) -> date | None:
    """조회 전용 세션이면 조회 가능한 마지막 날"""
    value = session.get("ro_until")
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


class RequireStaffRole:
    """허용한 역할(또는 ADMIN)만 접근 가능하도록 강제하는 FastAPI Dependency.
    ADMIN은 주방/홀 화면에도 접근할 수 있다. 통과하면 세션({store_id, role, exp})을 돌려준다.
    매장은 요청 값이 아니라 이 세션의 store_id로 정한다.
    기본 PIN 변경 전 세션은 allow_pin_change_pending=True인 곳(PIN 변경 화면)에서만 통과한다.
    조회 전용 세션(서비스 종료 매장)은 READONLY_PATHS의 GET만 통과하고, 기간이 지나면 다시 로그인해야 한다.
    통과한 세션은 request.state.staff_session에도 넣어 템플릿이 쓸 수 있게 한다."""

    def __init__(self, allowed_roles: list[Role], *, allow_pin_change_pending: bool = False):
        self.login_role = allowed_roles[0]  # 미로그인 시 로그인 화면에서 미리 선택할 역할
        self.allowed_roles = frozenset(r.value for r in allowed_roles)
        self.allow_pin_change_pending = allow_pin_change_pending

    def __call__(self, request: Request) -> Dict[str, Any]:
        try:
            session = get_current_staff_session(request)
        except HTTPException as e:
            raise _staff_denied(
                request, login_role=self.login_role, allowed=self.allowed_roles,
                status_code=status.HTTP_401_UNAUTHORIZED, detail=e.detail,
            ) from None
        if session["role"] == Role.ADMIN.value or session["role"] in self.allowed_roles:
            until = readonly_until_of(session)
            if until:
                if business_today() > until:
                    raise _staff_denied(
                        request, login_role=self.login_role, allowed=self.allowed_roles,
                        status_code=status.HTTP_401_UNAUTHORIZED, detail="조회 기간이 끝났습니다.",
                    )
                path = request.url.path
                allowed = (request.method in ("GET", "HEAD") and path in READONLY_PATHS) or (
                    request.method == "POST" and path in READONLY_POST_PATHS
                )
                if not allowed:
                    raise _readonly_denied(request)
            elif session.get("pin_change") and not self.allow_pin_change_pending:
                raise _pin_change_required(request)
            request.state.staff_session = session
            return session
        raise _staff_denied(
            request, login_role=self.login_role, allowed=self.allowed_roles,
            status_code=status.HTTP_403_FORBIDDEN, detail="해당 화면에 접근할 권한이 없습니다.",
        )


# 라우터에서 같은 인스턴스를 재사용한다 (FastAPI가 요청당 한 번만 실행하도록 캐시 키가 같아야 함)
require_admin = RequireStaffRole([Role.ADMIN])
require_kitchen = RequireStaffRole([Role.KITCHEN])
require_hall = RequireStaffRole([Role.HALL])
require_staff = RequireStaffRole(list(Role))  # 관리자/주방/홀 누구나
require_admin_pin_setup = RequireStaffRole([Role.ADMIN], allow_pin_change_pending=True)  # PIN 변경 화면 전용


# =====================================================================
# 4. 시스템(슈퍼) 관리자 세션 (/super/*)
# =====================================================================
SUPER_ROLE = "super"  # 스태프 Role과 분리: ROLE_TARGETS 등 스태프 권한 표에는 존재하지 않는다


def verify_super_password(plain: str) -> bool:
    expected = settings.SUPER_ADMIN_PASSWORD
    if not expected:
        return False
    return hmac.compare_digest(plain.encode("utf-8"), expected.encode("utf-8"))


def _super_fingerprint() -> str:
    """현재 비밀번호의 지문. 토큰에 넣어 두면 비밀번호를 바꿨을 때 기존 세션이 모두 무효가 된다."""
    key = settings.SECRET_KEY.encode("utf-8")
    msg = (settings.SUPER_ADMIN_PASSWORD or "").encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:16]


def create_super_session_token() -> str:
    expire = datetime.now(timezone.utc) + timedelta(seconds=settings.SUPER_SESSION_MAX_AGE_SECONDS)
    payload = {"role": SUPER_ROLE, "pwd": _super_fingerprint(), "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _super_login_required(request: Request) -> HTTPException:
    """요청 종류별 응답: 화면 이동은 로그인으로 리다이렉트, HTMX는 HX-Redirect, 그 외는 401"""
    if request.headers.get("HX-Request"):
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="시스템 관리자 로그인이 필요합니다.",
            headers={"HX-Redirect": "/super/login"},
        )
    if request.method == "GET":
        return HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="시스템 관리자 로그인이 필요합니다.",
            headers={"Location": f"/super/login?next={quote(request.url.path, safe='/')}"},
        )
    return _unauthorized("시스템 관리자 로그인이 필요합니다.")


def require_super_admin(request: Request) -> Dict[str, Any]:
    """/super/* 보호용 FastAPI Dependency.
    SUPER_ADMIN_PASSWORD가 설정되지 않았으면 모든 요청을 막는다(fail-closed)."""
    if not settings.SUPER_ADMIN_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="시스템 관리자 기능이 비활성화되어 있습니다. SUPER_ADMIN_PASSWORD를 설정하세요.",
        )
    token = request.cookies.get(settings.SUPER_SESSION_COOKIE_NAME)
    payload = None
    if token:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        except jwt.PyJWTError:
            payload = None
    if (
        not payload
        or payload.get("role") != SUPER_ROLE
        or not hmac.compare_digest(str(payload.get("pwd", "")), _super_fingerprint())
    ):
        raise _super_login_required(request)
    return payload