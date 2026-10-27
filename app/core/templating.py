from datetime import datetime, timezone

from fastapi.templating import Jinja2Templates

from app.core.clock import business_tz
from app.core.config import settings
from app.database.models import OrderStatus
from app.domain.order_status import (
    ROLE_TARGETS,
    ROLE_VIEW_STATUSES,
    STATUS_META,
    can_cancel,
    can_role_change,
)
from app.domain.inquiry import INQUIRY_CATEGORIES, mask_name
from app.domain.license import LICENSE_LABELS, readonly_until
from app.domain.menu import ITEM_FLAGS
from app.domain.roles import Role
from app.domain.table_status import TABLE_STATUS_META

templates = Jinja2Templates(directory=settings.TEMPLATES_DIR)


def _debug_print(*args, **kwargs) -> str:
    """템플릿 디버그용. DEBUG일 때만 출력하고, 항상 빈 문자열 반환(렌더에 'None'이 찍히지 않도록)."""
    if settings.DEBUG:
        print(*args, **kwargs)
    return ""


def staff_session(request) -> dict:
    """템플릿용: 이 요청의 스태프 세션 (RequireStaffRole이 넣어 둔 값, 없으면 빈 dict)"""
    return getattr(request.state, "staff_session", None) or {}


def support_new(request) -> int:
    """템플릿용: 매장이 아직 확인하지 않은 문의 답변 수 (routers/support.load_support_badge가 넣어 둔 값)"""
    return getattr(request.state, "support_new", 0)


def role_can(role: str, target) -> bool:
    """템플릿용: role('admin' 등)이 target 상태로 변경할 수 있는가. target은 enum 또는 문자열."""
    return can_role_change(Role(role), OrderStatus(target))


# 템플릿에서는 enum 키/멤버 해시 동작에 기대지 않도록 문자열 기반으로 노출한다.
templates.env.globals.update(
    STATUS_META=STATUS_META,
    can_cancel=can_cancel,
    role_can=role_can,
    ROLE_TARGETS={r.value: frozenset(t.value for t in ts) for r, ts in ROLE_TARGETS.items()},
    # 역할별 보드에 보이는 상태값 (생애주기 순서)
    ROLE_VIEW_STATUS_VALUES={
        r.value: [s.value for s in OrderStatus if s in sts]
        for r, sts in ROLE_VIEW_STATUSES.items()
    },
    ITEM_FLAGS=ITEM_FLAGS,
    TABLE_STATUS_META=TABLE_STATUS_META,  # 테이블 상태 표시명
    staff_session=staff_session,          # 조회 전용(ro_until) 여부 등
    support_new=support_new,              # 관리자 헤더 '문의' 배지
    readonly_until=readonly_until,        # 서비스 종료일 -> 조회 가능 마지막 날
    LICENSE_LABELS=LICENSE_LABELS,        # 라이선스 표시명 (체험/정식/종료)
    INQUIRY_CATEGORIES=INQUIRY_CATEGORIES,  # 문의 분류 표시명
    # 공개 화면(랜딩, 문의 게시판) 공통 정보
    SITE={
        "name": settings.PROJECT_NAME,
        "operator": settings.OPERATOR_NAME,
        "email": settings.CONTACT_EMAIL,
        "business_info": settings.BUSINESS_INFO,
    },  # 메뉴 상태 (관리자 토글 / 고객 배지)
    print=_debug_print,
)


def now() -> datetime:
    return datetime.now(timezone.utc)


def time_ago(dt: datetime | None) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    sec = int((datetime.now(timezone.utc) - dt).total_seconds())
    if sec < 60:
        return "방금 전"
    if sec < 3600:
        return f"{sec // 60}분 전"
    if sec < 86400:
        return f"{sec // 3600}시간 전"
    return f"{sec // 86400}일 전"


templates.env.filters["time_ago"] = time_ago


def local_dt(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """영업 시간대 기준 날짜/시각 표시"""
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(business_tz()).strftime(fmt)


templates.env.filters["local_dt"] = local_dt
templates.env.filters["mask_name"] = mask_name


def render_card(request, order, role: str):
    """공통 주문 카드 1장 렌더 (HTMX outerHTML swap 응답용)"""
    return templates.TemplateResponse(
        request=request,
        name="common/order_card.html",
        context={"order": order, "role": role},
    )
