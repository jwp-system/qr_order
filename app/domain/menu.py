"""메뉴 상태 도메인: 메뉴(Item)에 켜고 끌 수 있는 상태 플래그.

관리자 메뉴 화면의 토글 버튼과 고객 메뉴의 배지가 모두 이 표를 기준으로 그려진다.
키는 Item 모델의 Boolean 컬럼명과 같다.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ItemFlag:
    label: str          # 버튼/배지 표시명
    desc: str           # 관리자용 설명
    customer_badge: bool  # 고객 메뉴에 배지로 보여줄지


ITEM_FLAGS: dict[str, ItemFlag] = {
    "is_sold_out": ItemFlag("품절", "메뉴에는 보이지만 주문할 수 없습니다.", customer_badge=True),
    "is_hidden": ItemFlag("숨김", "고객 메뉴에서 감춥니다 (판매 중지).", customer_badge=False),
    "is_popular": ItemFlag("인기", "인기 배지를 표시합니다.", customer_badge=True),
    "is_new": ItemFlag("NEW", "신메뉴 배지를 표시합니다.", customer_badge=True),
    "is_recommended": ItemFlag("추천", "추천 배지를 표시합니다.", customer_badge=True),
}
