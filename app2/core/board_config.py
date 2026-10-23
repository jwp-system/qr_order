"""보드(관리자/주방/홀) 화면 설정.

화면 표시용 값만 둔다. 보드에 어떤 상태를 보여줄지(ROLE_VIEW_STATUSES)와
정렬 기준은 domain/service 쪽 책임이므로 여기서 다루지 않는다.

common/board_layout.html, common/order_grid.html이 이 설정으로 렌더링된다.
목록 갱신은 폴링이 아니라 SSE 신호(core/events.py)로 한다.
"""
from app2.domain.roles import Role

CARD_TEMPLATE = "common/order_card.html"


def _board(
    role: Role,
    *,
    title: str,
    badge: str,
    empty_title: str,
    empty_desc: str,
    theme: dict[str, str],
    feed_url: str | None = None,
) -> dict:
    return {
        "role": role.value,  # 템플릿에서 문자열로 쓰이므로 .value 사용
        "title": title,
        "badge": badge,
        "feed_url": feed_url or f"/{role.value}/feed",
        "card_template": CARD_TEMPLATE,
        "empty_title": empty_title,
        "empty_desc": empty_desc,
        "theme": theme,
    }


# Tailwind 클래스는 스캔을 위해 완성된 문자열로 유지 (f-string으로 조합 금지)
BOARDS: dict[str, dict] = {
    Role.ADMIN.value: _board(
        Role.ADMIN,
        title="주문 현황",
        badge="매장 관리 모니터",
        feed_url="/admin/orders/feed",
        empty_title="현재 처리할 주문 내역이 없습니다.",
        empty_desc="새로운 주문이 들어오면 실시간으로 표시됩니다.",
        theme={
            "header": "bg-blue-600",
            "dot": "bg-blue-300",
            "badge": "bg-blue-800",
            "empty_icon_wrap": "bg-blue-50 text-blue-300",
        },
    ),
    Role.HALL.value: _board(
        Role.HALL,
        title="홀 서빙 보드",
        badge="서빙 대기 모니터",
        empty_title="현재 서빙 대기 중인 주문이 없습니다.",
        empty_desc="주방에서 조리가 완료되면 서빙 카드가 자동으로 표시됩니다.",
        theme={
            "header": "bg-emerald-800",
            "dot": "bg-emerald-300",
            "badge": "bg-emerald-900",
            "empty_icon_wrap": "bg-emerald-50 text-emerald-300",
        },
    ),
    Role.KITCHEN.value: _board(
        Role.KITCHEN,
        title="주방 조리 보드",
        badge="조리 대기 모니터",
        empty_title="현재 조리 대기 중인 주문이 없습니다.",
        empty_desc="새 주문이 들어오면 조리 카드가 자동으로 표시됩니다.",
        theme={
            "header": "bg-orange-500",
            "dot": "bg-orange-300",
            "badge": "bg-orange-900",
            "empty_icon_wrap": "bg-orange-50 text-orange-300",
        },
    ),
}