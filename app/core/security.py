# app/core/security.py
import enum
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

import jwt
from fastapi import Request, HTTPException, status, Depends
from app.core.config import settings


# --- 1. 매장 접근 역할 정의 ---
class Role(str, enum.Enum):
    ADMIN = "ADMIN"
    KITCHEN = "KITCHEN"
    HALL = "HALL"


# --- 2. PIN 코드 해싱 및 검증 ---
def hash_pin(pin: str) -> str:
    """단순 4자리 PIN을 SHA-256으로 해싱하여 저장/비교"""
    salted = f"{pin}:{settings.SECRET_KEY}"
    return hashlib.sha256(salted.encode("utf-8")).hexdigest()

def verify_pin(plain_pin: str, hashed_pin: str) -> bool:
    return hash_pin(plain_pin) == hashed_pin


# --- 3. 테이블 QR 서명 토큰 (HMAC-SHA256 기반) ---
def create_table_qr_token(store_id: int, table_id: int, table_number: int) -> str:
    """
    테이블 번호 변조를 방지하기 위해 store_id, table_id를 패킹하여 서명된 JWT 토큰 발급.
    QR 특성상 만료 기간을 길게 두거나 테이블 메타데이터 확인용으로 사용.
    """
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
        if payload.get("sub") != "table_access":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 테이블 토큰입니다.")
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="만료되었거나 위조된 QR 토큰입니다.")


# --- 4. 매장 스태프 세션 쿠키 관리 ---
def create_staff_session_token(store_id: int, role: Role) -> str:
    """관리자/주방/홀 핀코드 인증 성공 시 세션 JWT 생성"""
    expire = datetime.now(timezone.utc) + timedelta(seconds=settings.SESSION_MAX_AGE_SECONDS)
    payload = {
        "store_id": store_id,
        "role": role.value,
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def get_current_staff_session(request: Request) -> Dict[str, Any]:
    """쿠키에서 세션을 추출하고 파싱"""
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요한 페이지입니다."
        )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="세션이 만료되었습니다.")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 세션입니다.")


# --- 5. 권한 인가 의존성(RBAC Dependency) ---
class RequireStaffRole:
    """특정 역할(Admin, Kitchen, Hall)만 접근 가능하도록 강제하는 FastAPI Dependency"""
    def __init__(self, allowed_roles: list[Role]):
        self.allowed_roles = [r.value for r in allowed_roles]

    def __call__(self, session_data: Dict[str, Any] = Depends(get_current_staff_session)) -> Dict[str, Any]:
        user_role = session_data.get("role")
        # ADMIN은 모든 하위 페이지(주방, 홀) 접근 허용
        if user_role == Role.ADMIN.value or user_role in self.allowed_roles:
            return session_data

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="해당 화면에 접근할 권한이 없습니다."
        )