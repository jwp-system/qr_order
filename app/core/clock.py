"""영업 시간대(BUSINESS_TIMEZONE) 기준 날짜 계산 — '오늘 매출', 만료일, 주문 내역 날짜 필터 공용"""
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from app.core.config import settings


def business_tz() -> ZoneInfo:
    return ZoneInfo(settings.BUSINESS_TIMEZONE)


def business_today() -> date:
    return datetime.now(business_tz()).date()


def day_start_utc(day: date) -> datetime:
    """영업일 day의 0시를 UTC로 (DB의 timezone-aware 컬럼 비교용)"""
    return datetime.combine(day, time.min, tzinfo=business_tz()).astimezone(timezone.utc)
