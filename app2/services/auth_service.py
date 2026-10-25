"""스태프 인증 서비스 (매장 코드 + 역할 + PIN)"""
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app2.core.security import verify_pin
from app2.database.models import LicenseStatus, Store
from app2.domain.roles import Role

PIN_LENGTH = 4

_PIN_FIELD = {
    Role.ADMIN: "admin_pin",
    Role.KITCHEN: "kitchen_pin",
    Role.HALL: "hall_pin",
}

# 매장 코드가 틀렸는지 PIN이 틀렸는지 알려주지 않는다 (매장 코드 추측 방지)
INVALID_CREDENTIALS = "매장 코드 또는 PIN이 올바르지 않습니다."


class StaffAuthError(Exception):
    def __init__(self, message: str, status_code: int = 401, counts_as_failure: bool = True):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.counts_as_failure = counts_as_failure  # 실패 횟수 제한에 포함할지


def normalize_service_code(value: str) -> str:
    return (value or "").strip().upper()


def is_valid_pin(pin: str) -> bool:
    # str.isdigit()은 '²'나 아랍 숫자도 통과시키므로 ASCII로 한정
    return bool(pin) and len(pin) == PIN_LENGTH and pin.isascii() and pin.isdigit()


def safe_next_path(value: str | None) -> str | None:
    """로그인 후 이동할 경로. 같은 사이트의 절대 경로만 허용 (오픈 리다이렉트 방지)."""
    if not value or not value.startswith("/") or value.startswith("//"):
        return None
    if "\\" in value or "\r" in value or "\n" in value:
        return None
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return None
    return value


async def authenticate_staff(
    db: AsyncSession, service_code: str, role: Role, pin: str
) -> Store:
    """매장 코드 + 역할 + PIN 확인. 실패하면 StaffAuthError."""
    store = (
        await db.execute(select(Store).where(Store.service_code == service_code))
    ).scalar_one_or_none()

    stored_hash = getattr(store, _PIN_FIELD[role]) if store else None
    if not store or not verify_pin(pin, stored_hash):
        raise StaffAuthError(INVALID_CREDENTIALS, status_code=401)

    # PIN이 맞은 뒤에 확인한다 (코드/PIN을 모르는 사람에게 매장 상태가 드러나지 않도록)
    if store.license_status == LicenseStatus.EXPIRED:
        raise StaffAuthError(
            "라이선스가 만료되었습니다. 시스템 관리자에게 문의해 주세요.",
            status_code=403,
            counts_as_failure=False,
        )
    return store
