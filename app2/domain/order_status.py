"""주문 상태 도메인: 상태 메타, 전이 규칙, 화면/역할별 상태 그룹"""
from dataclasses import dataclass

from app2.database.models import OrderStatus
from app2.domain.roles import Role

S = OrderStatus


# =====================================================================
# 상태 메타
# =====================================================================
@dataclass(frozen=True)
class StatusMeta:
    label: str                       # 고객/화면 표시명
    step: int                        # 진행 단계 (프로그레스 바용)
    customer_desc: str               # 고객용 안내 문구
    internal_desc: str               # 직원용 설명
    next: OrderStatus | None         # 정상 흐름의 다음 상태 (None이면 종료)
    allowed: frozenset[OrderStatus]  # 전이 가능한 모든 상태


STATUS_META: dict[OrderStatus, StatusMeta] = {
    S.ORDERED: StatusMeta(
        label="주문 접수", step=1,
        customer_desc="주문이 접수되었습니다. 매장 확인 대기 중입니다.",
        internal_desc="신규 주문 생성됨. 매장 확인(수락) 필요.",
        next=S.ACCEPTED,
        allowed=frozenset({S.ACCEPTED, S.CANCELLED}),
    ),
    S.ACCEPTED: StatusMeta(
        label="조리 준비", step=1,
        customer_desc="매장에서 주문을 확인했습니다. 곧 조리를 시작합니다.",
        internal_desc="매장 수락 완료. 주방 조리 시작 대기.",
        next=S.COOKING,
        allowed=frozenset({S.COOKING, S.CANCELLED}),
    ),
    S.COOKING: StatusMeta(
        label="조리 중", step=2,
        customer_desc="주방에서 맛있게 조리 중입니다.",
        internal_desc="조리 진행 중. 완료 시 '조리 완료' 처리 필요.",
        next=S.COOKED,
        allowed=frozenset({S.COOKED, S.CANCELLED}),
    ),
    S.COOKED: StatusMeta(
        label="서빙 준비", step=2,
        customer_desc="조리가 완료되었습니다. 서빙을 준비 중입니다.",
        internal_desc="조리 완료. 홀 직원 서빙 대기.",
        next=S.SERVED,
        allowed=frozenset({S.SERVED, S.CANCELLED}),
    ),
    S.SERVED: StatusMeta(
        label="서빙 완료", step=3,
        customer_desc="음식이 준비되었습니다. 맛있게 드세요!",
        internal_desc="테이블 서빙 완료. 결제/주문 종료 대기.",
        next=S.COMPLETED,
        allowed=frozenset({S.COMPLETED, S.CANCELLED}),
    ),
    S.COMPLETED: StatusMeta(
        label="주문 완료", step=4,
        customer_desc="이용해 주셔서 감사합니다.",
        internal_desc="주문 종료(최종 상태). 상태 변경 불가.",
        next=None,
        allowed=frozenset(),
    ),
    S.CANCELLED: StatusMeta(
        label="주문 취소", step=0,
        customer_desc="매장 사정으로 주문이 취소되었습니다.",
        internal_desc="주문 취소됨(최종 상태). 환불/재고 처리 확인 필요.",
        next=None,
        allowed=frozenset(),
    ),
}


# =====================================================================
# 상태 그룹 (화면별 조회 대상)
# =====================================================================
TERMINAL_STATUSES = frozenset(s for s, m in STATUS_META.items() if not m.allowed)
ACTIVE_STATUSES = tuple(s for s in OrderStatus if s not in TERMINAL_STATUSES)

KITCHEN_STATUSES = (S.ACCEPTED, S.COOKING)   # 주방 KDS
HALL_STATUSES = (S.COOKED,)                  # 홀 서빙
# 고객 화면(주문 현황, 배지)에 보여줄 상태: 진행 중인 주문 전체
CUSTOMER_VISIBLE_STATUSES = ACTIVE_STATUSES  # ORDERED ~ SERVED


# =====================================================================
# 전이 검증
# =====================================================================
class InvalidTransition(Exception):
    def __init__(self, current: OrderStatus, target: OrderStatus):
        super().__init__(f"{current.value} -> {target.value} 전이는 허용되지 않습니다.")
        self.current, self.target = current, target


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    return target in STATUS_META[current].allowed


def validate_transition(current: OrderStatus, target: OrderStatus) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(current, target)


def next_status(current: OrderStatus) -> OrderStatus | None:
    return STATUS_META[current].next


def is_terminal(status: OrderStatus) -> bool:
    return status in TERMINAL_STATUSES


def can_cancel(status: OrderStatus) -> bool:
    return can_transition(status, S.CANCELLED)


def describe(status: OrderStatus, *, internal: bool = False) -> str:
    m = STATUS_META[status]
    return m.internal_desc if internal else m.customer_desc


# =====================================================================
# 역할별 처리 (서버 검증 + 템플릿 버튼 노출 공통 사용)
# =====================================================================
# 역할별로 변경할 수 있는 목표 상태
ROLE_TARGETS: dict[Role, frozenset[OrderStatus]] = {
    Role.ADMIN: frozenset({
        S.ACCEPTED, S.COOKING, S.COOKED, S.SERVED, S.COMPLETED, S.CANCELLED,
    }),
    Role.KITCHEN: frozenset({S.COOKING, S.COOKED}),
    Role.HALL: frozenset({S.SERVED}),
}

# 역할별 보드에 남아 있어야 하는 상태 (변경 후 여기에 없으면 카드 제거)
ROLE_VIEW_STATUSES: dict[Role, frozenset[OrderStatus]] = {
    Role.ADMIN: frozenset(ACTIVE_STATUSES),
    Role.KITCHEN: frozenset(KITCHEN_STATUSES),
    Role.HALL: frozenset(HALL_STATUSES),
}


def can_role_change(role: Role, target: OrderStatus) -> bool:
    return target in ROLE_TARGETS[role]


def stays_on_board(role: Role, status: OrderStatus) -> bool:
    return status in ROLE_VIEW_STATUSES[role]