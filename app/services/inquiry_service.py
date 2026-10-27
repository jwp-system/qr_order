"""문의 서비스 (공개 게시판, 매장 관리자 사용 중 문의, 시스템 관리자 답변).

  - 공개 게시판(/inquiries): store_id 없는 글만. 비밀글은 작성자가 정한 비밀번호로만 열린다.
  - 매장 문의(/admin/support): store_id 있는 글. 로그인한 매장만 보고, 게시판에는 나오지 않는다.
공개 화면에는 연락처를 절대 내보내지 않는다.
폼 값 검증 실패는 InquiryError(메시지)로 올리고, 라우터가 입력값을 유지한 채 폼을 다시 보여준다.
"""
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import hash_password, verify_password
from app.database.models import Inquiry, InquiryCategory, Store

PAGE_SIZE = 20
STORE_LIST_LIMIT = 100
# 매장 관리자 화면에서 고를 수 있는 분류 (도입/트라이얼은 이미 쓰는 매장에 해당 없음)
STORE_CATEGORIES = (InquiryCategory.SUPPORT, InquiryCategory.FEATURE, InquiryCategory.ETC)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InquiryError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class InquiryPage:
    items: list[Inquiry]
    total: int
    page: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // PAGE_SIZE))


def _text(form: Mapping, key: str, label: str, max_len: int, *, required: bool = True) -> str | None:
    value = (form.get(key) or "").strip()
    if not value:
        if required:
            raise InquiryError(f"{label}을(를) 입력해 주세요.")
        return None
    if len(value) > max_len:
        raise InquiryError(f"{label}은(는) {max_len:,}자 이하로 입력해 주세요.")
    return value


def _contact(form: Mapping) -> str:
    """전화번호(숫자 9~15자리) 또는 이메일"""
    value = _text(form, "contact", "연락처", 100)
    digits = re.sub(r"[\s\-()+]", "", value)
    if _EMAIL.match(value) or (digits.isdigit() and 9 <= len(digits) <= 15):
        return value
    raise InquiryError("연락처는 전화번호나 이메일 주소로 입력해 주세요.")


# =====================================================================
# 작성 (공개 게시판 / 매장 관리자)
# =====================================================================
async def create(db: AsyncSession, form: Mapping, *, store: Store | None = None) -> Inquiry:
    """store가 있으면 매장 문의: 항상 비공개, 비밀번호 대신 로그인한 매장으로만 열람"""
    try:
        category = InquiryCategory(form.get("category") or "")
    except ValueError:
        raise InquiryError("문의 분류를 선택해 주세요.") from None
    if store and category not in STORE_CATEGORIES:
        raise InquiryError("문의 분류를 선택해 주세요.")
    inquiry = Inquiry(
        category=category,
        author_name=_text(form, "author_name", "이름", 30),
        store_name=_text(form, "store_name", "매장명", 100, required=False),
        contact=_contact(form),
        title=_text(form, "title", "제목", 100),
        content=_text(form, "content", "내용", 2000),
        is_private=form.get("is_private") in ("on", "true", "1"),
    )
    if store:
        inquiry.store_id = store.id
        inquiry.store_name = store.name
        inquiry.is_private = True
        password = secrets.token_urlsafe(16)  # 게시판으로는 열 수 없게 (아무도 모르는 값)
    else:
        password = form.get("password") or ""
        if not 4 <= len(password) <= 64:
            raise InquiryError("비밀번호는 4자 이상으로 입력해 주세요.")
    if form.get("agree") not in ("on", "true", "1"):
        raise InquiryError("개인정보 수집·이용에 동의해 주세요.")
    inquiry.password_hash = hash_password(password)
    db.add(inquiry)
    await db.commit()
    return inquiry


# =====================================================================
# 조회
# =====================================================================
async def list_page(
    db: AsyncSession, page: int, category: InquiryCategory | None = None, *,
    unanswered: bool = False, public: bool = False,
) -> InquiryPage:
    """public=True: 공개 게시판 글만 (매장 문의 제외). 시스템 관리자는 전체."""
    conditions = []
    if public:
        conditions.append(Inquiry.store_id.is_(None))
    if category:
        conditions.append(Inquiry.category == category)
    if unanswered:
        conditions.append(Inquiry.answer.is_(None))
    total = (await db.execute(select(func.count()).select_from(Inquiry).where(*conditions))).scalar_one()
    page = min(max(1, page), max(1, -(-total // PAGE_SIZE)))
    items = (
        await db.execute(
            select(Inquiry).options(selectinload(Inquiry.store)).where(*conditions)
            .order_by(Inquiry.created_at.desc(), Inquiry.id.desc())
            .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
        )
    ).scalars().all()
    return InquiryPage(list(items), total, page)


async def get(db: AsyncSession, inquiry_id: int, *, public: bool = False) -> Inquiry:
    """public=True: 공개 게시판에서 여는 경우. 매장 문의는 없는 글처럼 404."""
    inquiry = await db.get(Inquiry, inquiry_id)
    if not inquiry or (public and inquiry.store_id is not None):
        raise HTTPException(status_code=404, detail="문의를 찾을 수 없습니다.")
    return inquiry


# =====================================================================
# 매장 관리자 (/admin/support)
# =====================================================================
async def store_inquiries(db: AsyncSession, store_id: int) -> list[Inquiry]:
    return list((
        await db.execute(
            select(Inquiry).where(Inquiry.store_id == store_id)
            .order_by(Inquiry.created_at.desc(), Inquiry.id.desc()).limit(STORE_LIST_LIMIT)
        )
    ).scalars().all())


def _unseen_answer(store_id: int):
    return (
        Inquiry.store_id == store_id,
        Inquiry.answered_at.is_not(None),
        or_(Inquiry.answer_seen_at.is_(None), Inquiry.answer_seen_at < Inquiry.answered_at),
    )


async def new_answer_count(db: AsyncSession, store_id: int) -> int:
    """매장이 아직 확인하지 않은 답변 수 (관리자 헤더 배지)"""
    return (await db.execute(select(func.count()).select_from(Inquiry).where(*_unseen_answer(store_id)))).scalar_one()


async def mark_answers_seen(db: AsyncSession, store_id: int) -> None:
    await db.execute(
        update(Inquiry).where(*_unseen_answer(store_id))
        .values(answer_seen_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    await db.commit()


async def last_author(db: AsyncSession, store_id: int) -> dict:
    """작성 칸 미리 채우기용: 이 매장이 마지막으로 남긴 이름/연락처"""
    row = (
        await db.execute(
            select(Inquiry.author_name, Inquiry.contact).where(Inquiry.store_id == store_id)
            .order_by(Inquiry.created_at.desc()).limit(1)
        )
    ).first()
    return {"author_name": row[0], "contact": row[1]} if row else {}


def can_view(inquiry: Inquiry, password: str | None) -> bool:
    """공개글은 누구나, 비밀글은 비밀번호가 맞을 때만"""
    if not inquiry.is_private:
        return True
    return bool(password) and verify_password(password, inquiry.password_hash)


# =====================================================================
# 시스템 관리자
# =====================================================================
async def unanswered_count(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(Inquiry).where(Inquiry.answer.is_(None)))).scalar_one()


async def answer(db: AsyncSession, inquiry: Inquiry, text: str) -> None:
    """답변 등록/수정. 빈 답변이면 답변을 지우고 '답변 대기'로 되돌린다."""
    text = (text or "").strip()
    if len(text) > 4000:
        raise InquiryError("답변은 4,000자 이하로 입력해 주세요.")
    inquiry.answer = text or None
    inquiry.answered_at = datetime.now(timezone.utc) if text else None
    await db.commit()


async def delete(db: AsyncSession, inquiry: Inquiry) -> None:
    await db.delete(inquiry)
    await db.commit()
