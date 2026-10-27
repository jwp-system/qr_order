"""매장 라이선스 도메인: 서비스 종료(EXPIRED) 후 조회 전용 기간.

  TRIAL / ACTIVE   정상 이용
  EXPIRED          만료일(expired_on)부터 1개월 되는 날까지 관리자만 조회 전용 로그인
                   (주문 내역, 메뉴 보기). 주방/홀 로그인과 손님 QR 주문은 불가.
                   그 뒤로는 로그인 불가.
날짜 단위로 처리한다 (영업 시간대 기준 날짜, core/clock.py).
"""
import calendar
import enum
from datetime import date

from app.database.models import LicenseStatus

READONLY_MONTHS = 1

# 화면 표시명 (관리자 헤더 등)
LICENSE_LABELS: dict[LicenseStatus, str] = {
    LicenseStatus.TRIAL: "체험",
    LicenseStatus.ACTIVE: "정식",
    LicenseStatus.EXPIRED: "종료",
}


class StoreAccess(str, enum.Enum):
    FULL = "full"            # 정상 이용
    READONLY = "readonly"    # 종료 후 조회 기간
    CLOSED = "closed"        # 조회 기간도 끝남


def add_months(day: date, months: int) -> date:
    """같은 날짜로 n개월 뒤. 그 달에 없는 날이면 말일 (1/31 + 1개월 = 2/28)."""
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def readonly_until(expired_on: date) -> date:
    """조회 가능한 마지막 날 (이날까지 포함)"""
    return add_months(expired_on, READONLY_MONTHS)


def store_access(status: LicenseStatus, expired_on: date | None, today: date) -> StoreAccess:
    if status is not LicenseStatus.EXPIRED:
        return StoreAccess.FULL
    # 만료일 기록이 없는 옛 데이터는 오늘 만료된 것으로 본다 (조회 기간을 빼앗지 않도록)
    if today <= readonly_until(expired_on or today):
        return StoreAccess.READONLY
    return StoreAccess.CLOSED
