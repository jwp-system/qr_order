"""손님에게 보여줄 공개 주소 (테이블 QR, 랜딩 데모 QR 공용)"""
from urllib.parse import quote, urlsplit

from fastapi import Request

from app.core.config import settings

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def public_base_url(request: Request) -> str:
    """PUBLIC_BASE_URL이 없으면 지금 접속한 주소를 쓴다"""
    return (settings.PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")


def order_url(request: Request, qr_token: str) -> str:
    """손님이 QR로 여는 주문 화면 주소"""
    return f"{public_base_url(request)}/order/{quote(qr_token, safe='')}"


def is_local(request: Request) -> bool:
    """공개 주소가 휴대폰에서 열 수 없는 로컬 주소인가 (화면 경고용)"""
    return urlsplit(public_base_url(request)).hostname in _LOCAL_HOSTS
