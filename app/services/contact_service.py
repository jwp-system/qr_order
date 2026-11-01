"""회사 문의 (/contact 폼, /contact/board 공개 목록, /super/contacts 관리).

QR Order 문의 게시판(inquiry_service, /inquiries)과 별개다.
공개 목록에는 종류·가린 이름·날짜·처리 상태만 내보내고, 내용과 연락처는 시스템 관리자만 본다.
폼 값 검증 실패는 ContactError(메시지)로 올리고, 라우터가 입력값을 유지한 채 폼을 다시 보여준다.
"""
import re
from collections.abc import Mapping
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ContactMessage

PAGE_SIZE = 20
# 문의 종류: 폼 값(?type=) -> 표시명
CONTACT_TOPICS: dict[str, str] = {
    "qr": "QR Order (식당·주점)",
    "academy": "Academy (학원)",
    "erp": "바로ERP 파일럿 사전 신청",
    "custom": "맞춤 구축 상담",
    "etc": "기타 문의",
}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# 한 줄 입력칸 최대 길이 (컬럼 길이와 같음)
_FIELDS = {"name": 30, "company": 100, "email": 100, "phone": 30, "scale": 100}
CONTENT_MAX = 3000
MEMO_MAX = 4000


class ContactError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class ContactPage:
    items: list[ContactMessage]
    total: int
    page: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // PAGE_SIZE))


def parse(form: Mapping, *, require_agree: bool = True) -> dict:
    """폼 -> ContactMessage 컬럼 값. 한 줄 칸은 공백을 하나로 줄이고, 빈 선택 칸은 None."""
    values = {k: " ".join(str(form.get(k) or "").split()) for k in _FIELDS}
    content = str(form.get("content") or "").strip()
    topic = str(form.get("topic") or "")
    if topic not in CONTACT_TOPICS:
        raise ContactError("문의 종류를 골라 주세요.")
    if not values["name"]:
        raise ContactError("이름을 입력해 주세요.")
    if not _EMAIL.match(values["email"]):
        raise ContactError("답장 받을 이메일 주소를 정확히 입력해 주세요.")
    if not content:
        raise ContactError("문의 내용을 입력해 주세요.")
    if require_agree and not form.get("agree"):
        raise ContactError("개인정보 수집·이용에 동의해 주세요.")
    for key, limit in _FIELDS.items():
        if len(values[key]) > limit:
            raise ContactError(f"입력이 너무 깁니다 ({limit}자 이내).")
    if len(content) > CONTENT_MAX:
        raise ContactError(f"문의 내용은 {CONTENT_MAX:,}자 이내로 입력해 주세요.")
    return {**{k: v or None for k, v in values.items()}, "topic": topic, "content": content}


async def create(db: AsyncSession, form: Mapping) -> ContactMessage:
    contact = ContactMessage(**parse(form))
    db.add(contact)
    await db.commit()
    return contact


async def list_page(db: AsyncSession, page: int, *, only_open: bool = False) -> ContactPage:
    conditions = [ContactMessage.is_done.is_(False)] if only_open else []
    total = (await db.execute(select(func.count()).select_from(ContactMessage).where(*conditions))).scalar_one()
    page = min(max(1, page), max(1, -(-total // PAGE_SIZE)))
    items = (
        await db.execute(
            select(ContactMessage).where(*conditions)
            .order_by(ContactMessage.created_at.desc(), ContactMessage.id.desc())
            .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
        )
    ).scalars().all()
    return ContactPage(list(items), total, page)


async def open_count(db: AsyncSession) -> int:
    return (await db.execute(
        select(func.count()).select_from(ContactMessage).where(ContactMessage.is_done.is_(False))
    )).scalar_one()


async def get(db: AsyncSession, contact_id: int) -> ContactMessage:
    contact = await db.get(ContactMessage, contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다.")
    return contact


async def update(db: AsyncSession, contact: ContactMessage, form: Mapping) -> None:
    """시스템 관리자 수정: 문의 내용 전부 + 메모 + 처리 완료"""
    values = parse(form, require_agree=False)
    memo = str(form.get("memo") or "").strip()
    if len(memo) > MEMO_MAX:
        raise ContactError(f"메모는 {MEMO_MAX:,}자 이내로 입력해 주세요.")
    for key, value in values.items():
        setattr(contact, key, value)
    contact.memo = memo or None
    contact.is_done = bool(form.get("is_done"))
    await db.commit()


async def delete(db: AsyncSession, contact: ContactMessage) -> None:
    await db.delete(contact)
    await db.commit()
