# app2/core/security.py
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from urllib.parse import quote

import jwt
from fastapi import Depends, HTTPException, Request, status

from app2.core.config import settings
from app2.domain.roles import Role  # security.Role 로도 import 가능 (하위 호환)


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


# =====================================================================
# 2. 테이블 QR 서명 토큰 (JWT, HS256)
# =====================================================================
def create_table_qr_token(store_id: int, table_id: int, table_number: int) -> str:
    """store_id/table_id를 패킹해 서명한 토큰 발급 (변조 방지).
    QR 특성상 만료 없이 발급한다."""
    payload = {
        "sub": "table_access",
        "store_id": store_id,
        "table_id": table_id,
        "table_number": table_number,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_table_qr_token(token: str) -> Dict[str, Any]:
    """고객 접근 시 QR 토큰 무결성 검증"""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except jwt.PyJWTError:
        raise _unauthorized("만료되었거나 위조된 QR 토큰입니다.")
    if payload.get("sub") != "table_access":
        raise _unauthorized("유효하지 않은 테이블 토큰입니다.")
    return payload


# =====================================================================
# 3. 매장 스태프 세션 쿠키
# =====================================================================
def create_staff_session_token(store_id: int, role: Role) -> str:
    """관리자/주방/홀 PIN 인증 성공 시 세션 JWT 생성"""
    expire = datetime.now(timezone.utc) + timedelta(seconds=settings.SESSION_MAX_AGE_SECONDS)
    payload = {"store_id": store_id, "role": role.value, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def get_current_staff_session(request: Request) -> Dict[str, Any]:
    """쿠키에서 세션을 추출하고 파싱"""
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not token:
        raise _unauthorized("로그인이 필요한 페이지입니다.")
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise _unauthorized("세션이 만료되었습니다.")
    except jwt.PyJWTError:
        raise _unauthorized("유효하지 않은 세션입니다.")
    # 같은 키로 서명된 다른 종류의 토큰(슈퍼 관리자 등)은 스태프 세션으로 인정하지 않는다
    if payload.get("role") not in _STAFF_ROLES or not isinstance(payload.get("store_id"), int):
        raise _unauthorized("유효하지 않은 세션입니다.")
    return payload


# =====================================================================
# 4. 권한 인가 의존성 (RBAC)
# =====================================================================
class RequireStaffRole:
    """허용한 역할(또는 ADMIN)만 접근 가능하도록 강제하는 FastAPI Dependency.
    ADMIN은 주방/홀 화면에도 접근할 수 있다."""

    def __init__(self, allowed_roles: list[Role]):
        self.allowed_roles = frozenset(r.value for r in allowed_roles)

    def __call__(
        self, session_data: Dict[str, Any] = Depends(get_current_staff_session)
    ) -> Dict[str, Any]:
        user_role = session_data.get("role")
        if user_role == Role.ADMIN.value or user_role in self.allowed_roles:
            return session_data

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="해당 화면에 접근할 권한이 없습니다.",
        )


# =====================================================================
# 5. 시스템(슈퍼) 관리자 세션 (/super/*)
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