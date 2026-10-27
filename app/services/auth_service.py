"""스태프 인증 서비스 (매장 코드 + 역할 + PIN)"""
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import business_today
from app.core.security import hash_pin, verify_pin
from app.database.models import Store
from app.domain.license import StoreAccess, readonly_until, store_access
from app.domain.roles import Role

PIN_LENGTH = 4

_PIN_FIELD = {
    Role.ADMIN: "admin_pin",
    Role.KITCHEN: "kitchen_pin",
    Role.HALL: "hall_pin",
}

# 매장 생성 / PIN 초기화 시 쓰는 기본 PIN. 이 값이 남아 있으면 관리자 로그인 시 변경을 강제한다.
DEFAULT_PINS = {
    Role.ADMIN: "1234",
    Role.KITCHEN: "0000",
    Role.HALL: "1111",
}

# 매장 코드가 틀렸는지 PIN이 틀렸는지 알려주지 않는다 (매장 코드 추측 방지)
INVALID_CREDENTIALS = "매장 코드 또는 PIN이 올바르지 않습니다."


class PinChangeError(Exception):
    def __init__(self, message: str, counts_as_failure: bool = False):
        super().__init__(message)
        self.message = message
        self.counts_as_failure = counts_as_failure  # 현재 PIN 오류만 실패 횟수 제한에 포함


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
) -> tuple[Store, StoreAccess]:
    """매장 코드 + 역할 + PIN 확인. 실패하면 StaffAuthError.
    서비스 종료 매장은 조회 기간 안에 관리자만 READONLY로 통과한다."""
    store = (
        await db.execute(select(Store).where(Store.service_code == service_code))
    ).scalar_one_or_none()

    stored_hash = getattr(store, _PIN_FIELD[role]) if store else None
    if not store or not verify_pin(pin, stored_hash):
        raise StaffAuthError(INVALID_CREDENTIALS, status_code=401)

    # PIN이 맞은 뒤에 확인한다 (코드/PIN을 모르는 사람에게 매장 상태가 드러나지 않도록)
    access = store_access(store.license_status, store.expired_on, business_today())
    if access is StoreAccess.CLOSED:
        raise StaffAuthError(
            "서비스가 종료되었고 조회 기간도 끝났습니다. 시스템 관리자에게 문의해 주세요.",
            status_code=403,
            counts_as_failure=False,
        )
    if access is StoreAccess.READONLY and role is not Role.ADMIN:
        until = readonly_until(store.expired_on or business_today())
        raise StaffAuthError(
            f"서비스가 종료된 매장입니다. {until:%Y-%m-%d}까지 관리자만 주문 내역과 메뉴를 조회할 수 있습니다.",
            status_code=403,
            counts_as_failure=False,
        )
    return store, access


# =====================================================================
# 기본 PIN 확인 / PIN 변경
# =====================================================================
def default_pin_roles(store: Store) -> list[Role]:
    """아직 기본 PIN을 쓰고 있는 역할들. 해시가 결정적이라 저장된 값과 기본 PIN 해시를 비교하면 된다."""
    return [
        role for role, default in DEFAULT_PINS.items()
        if verify_pin(default, getattr(store, _PIN_FIELD[role]))
    ]


async def change_pins(
    db: AsyncSession, store: Store, current_admin_pin: str, new_pins: dict[Role, str]
) -> list[Role]:
    """현재 관리자 PIN 확인 후 PIN 변경. new_pins에 없는(빈) 역할은 유지한다.
    기본 PIN이 남은 역할은 반드시 새 PIN을 받아야 한다. 실패하면 PinChangeError. 바뀐 역할 목록을 돌려준다."""
    if not verify_pin(current_admin_pin, store.admin_pin):
        raise PinChangeError("현재 관리자 PIN이 올바르지 않습니다.", counts_as_failure=True)

    labels = {Role.ADMIN: "관리자", Role.KITCHEN: "주방", Role.HALL: "홀"}
    new_pins = {role: pin for role, pin in new_pins.items() if pin}

    missing = [labels[r] for r in default_pin_roles(store) if r not in new_pins]
    if missing:
        raise PinChangeError(f"기본 PIN을 사용 중인 {', '.join(missing)} PIN을 새로 입력해 주세요.")
    if not new_pins:
        raise PinChangeError("변경할 PIN을 입력해 주세요.")

    defaults = set(DEFAULT_PINS.values())
    for role, pin in new_pins.items():
        if not is_valid_pin(pin):
            raise PinChangeError(f"{labels[role]} PIN은 숫자 {PIN_LENGTH}자리입니다.")
        if pin in defaults:
            raise PinChangeError(f"{labels[role]} PIN에 기본 PIN({', '.join(sorted(defaults))})은 쓸 수 없습니다.")

    # 관리자 PIN이 주방/홀 PIN과 같으면 주방/홀 직원이 관리자로 로그인할 수 있다
    final = {
        role: hash_pin(new_pins[role]) if role in new_pins else getattr(store, _PIN_FIELD[role])
        for role in DEFAULT_PINS
    }
    if final[Role.ADMIN] in (final[Role.KITCHEN], final[Role.HALL]):
        raise PinChangeError("관리자 PIN은 주방/홀 PIN과 달라야 합니다.")

    for role in new_pins:
        setattr(store, _PIN_FIELD[role], final[role])
    await db.commit()
    return list(new_pins)
