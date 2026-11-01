"""회사 문의(/contact) 알림 메일 발송.

받는 주소는 CONTACT_EMAIL, 보내는 계정은 SMTP_USER (Gmail은 앱 비밀번호). 답장은 Reply-To로 문의한 사람에게 간다.
문의는 먼저 DB에 저장되므로(contact_service) 발송은 알림일 뿐이다. 미설정·실패는 로그만 남기고 False를 돌려준다.
"""
import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings
from app.database.models import ContactMessage
from app.services.contact_service import CONTACT_TOPICS

log = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(settings.SMTP_USER and settings.SMTP_PASSWORD)


def _build(c: ContactMessage, admin_url: str) -> EmailMessage:
    msg = EmailMessage()
    who = f"{c.name} / {c.company}" if c.company else c.name
    msg["Subject"] = f"[JWP-SYSTEM 문의 #{c.id}] {CONTACT_TOPICS[c.topic]} - {who}"
    msg["From"] = settings.SMTP_USER
    msg["To"] = settings.CONTACT_EMAIL
    msg["Reply-To"] = c.email
    msg.set_content(
        f"문의 종류: {CONTACT_TOPICS[c.topic]}\n"
        f"이름: {c.name}\n"
        f"회사·사업장: {c.company or '-'}\n"
        f"이메일: {c.email}\n"
        f"전화: {c.phone or '-'}\n"
        f"업종·규모: {c.scale or '-'}\n"
        f"\n{c.content}\n"
        f"\n관리 화면: {admin_url}\n"
    )
    return msg


def _send_sync(msg: EmailMessage) -> None:
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
        smtp.starttls()
        smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        smtp.send_message(msg)


async def notify_contact(c: ContactMessage, admin_url: str) -> bool:
    if not is_configured():
        log.warning("SMTP 미설정: 문의 #%s 알림 메일을 보내지 않음", c.id)
        return False
    try:
        await asyncio.to_thread(_send_sync, _build(c, admin_url))
    except (smtplib.SMTPException, OSError):
        log.exception("문의 #%s 알림 메일 발송 실패", c.id)
        return False
    return True
