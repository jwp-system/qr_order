"""문의 게시판 도메인: 분류 표시명, 작성자 이름 가리기"""
from dataclasses import dataclass

from app.database.models import InquiryCategory


@dataclass(frozen=True)
class CategoryMeta:
    label: str
    desc: str      # 작성 화면 안내
    query: str     # /inquiries/new?type=... 에 쓰는 짧은 키


INQUIRY_CATEGORIES: dict[InquiryCategory, CategoryMeta] = {
    InquiryCategory.INTRO: CategoryMeta("도입 문의", "우리 매장에 맞는지, 도입 절차와 요금이 궁금하실 때", "intro"),
    InquiryCategory.TRIAL: CategoryMeta("트라이얼·데모 요청", "파일럿(무료 체험) 신청이나 데모 매장 체험을 원하실 때", "trial"),
    InquiryCategory.SUPPORT: CategoryMeta("사용 중 문의", "이미 사용 중인 매장의 사용법·오류 문의", "support"),
    InquiryCategory.FEATURE: CategoryMeta("기능 요청", "있었으면 하는 기능이나 개선 의견", "feature"),
    InquiryCategory.ETC: CategoryMeta("기타", "그 밖의 문의", "etc"),
}
CATEGORY_BY_QUERY = {meta.query: cat for cat, meta in INQUIRY_CATEGORIES.items()}


def mask_name(name: str) -> str:
    """공개 목록용: 첫 글자만 남기고 가린다 (박종욱 -> 박**, A -> A*)"""
    name = (name or "").strip()
    if not name:
        return "익명"
    return name[0] + "*" * max(1, len(name) - 1)
