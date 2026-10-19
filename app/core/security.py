# app/core/security.py
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import jwt
from fastapi import Depends, HTTPException, Request, status

from app.core.config import settings
from app.domain.roles import Role  # security.Role 로도 import 가능 (하위 호환)


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
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise _unauthorized("세션이 만료되었습니다.")
    except jwt.PyJWTError:
        raise _unauthorized("유효하지 않은 세션입니다.")


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