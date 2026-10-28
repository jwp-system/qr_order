"""랜딩 '주문 입력 테스트' 데모 (데모 전용 매장: DEMO_SERVICE_CODE, 데모 QR: DEMO_QR_TOKEN).

방문자가 손님 화면에서 주문하고, 랜딩의 공개 주방 보드에서 접수 → 조리 → 서빙 → 마감까지 직접 넘겨 본다.
공개 API(routers/demo.py)는 이 매장의 주문만 다룬다. 운영 매장에 영향을 주지 않도록:
  - 데모 주문은 ORDER_TTL이 지나면 자동 삭제 (cleanup_forever, main.py lifespan에서 실행)
  - 주문당 수량 상한 (check_order_limits), IP당 주문 횟수 제한 (routers/client.py)
  - 공개 보드에는 요청사항을 보여주지 않는다 (방문자가 아무 글이나 쓸 수 있으므로)
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.events import notify_order_changed
from app.core.urls import order_url
from app.database.models import LicenseStatus, Order, OrderItem, OrderStatus, Store
from app.database.session import AsyncSessionLocal
from app.domain.order_status import ACTIVE_STATUSES, next_status
from app.domain.roles import Role
from app.services import client_service, store_service

logger = logging.getLogger(__name__)

ORDER_TTL = timedelta(minutes=30)
CLEANUP_INTERVAL_SECONDS = 300
FEED_LIMIT = 8
MAX_QUANTITY_PER_ORDER = 10

# 공개 보드 버튼: 현재 상태 -> 다음 단계로 넘기는 버튼 이름
NEXT_ACTION = {
    OrderStatus.ORDERED: "주문 접수",
    OrderStatus.ACCEPTED: "조리 시작",
    OrderStatus.COOKING: "조리 완료",
    OrderStatus.COOKED: "서빙 완료",
    OrderStatus.SERVED: "마감 (정산)",
}


def enabled() -> bool:
    return bool(settings.DEMO_SERVICE_CODE and settings.DEMO_QR_TOKEN)


async def demo_store(db: AsyncSession) -> Store | None:
    """데모 매장. 설정이 없거나 매장이 없거나 종료(EXPIRED)면 None"""
    if not enabled():
        return None
    store = (
        await db.execute(select(Store).where(Store.service_code == settings.DEMO_SERVICE_CODE))
    ).scalar_one_or_none()
    if not store or store.license_status is LicenseStatus.EXPIRED:
        return None
    return store


async def require_demo_store(db: AsyncSession) -> Store:
    store = await demo_store(db)
    if not store:
        raise HTTPException(status_code=404, detail="데모가 설정되어 있지 않습니다.")
    return store


async def landing_context(request: Request, db: AsyncSession) -> dict | None:
    """랜딩 데모 섹션 정보. 데모 QR 테이블이 데모 매장 것이 아니거나 열 수 없으면 None"""
    store = await demo_store(db)
    if not store:
        return None
    try:
        table = await client_service.get_active_table_by_token(db, settings.DEMO_QR_TOKEN)
    except HTTPException:
        return None
    if table.store_id != store.id:
        return None
    return {"order_url": order_url(request, settings.DEMO_QR_TOKEN), "table_number": table.table_number}


def is_demo(store_id: int, store: Store | None) -> bool:
    return store is not None and store.id == store_id


# =====================================================================
# 공개 주방 보드
# =====================================================================
async def feed_orders(db: AsyncSession, store_id: int) -> list[Order]:
    """진행 중 주문 (최신순, FEED_LIMIT건)"""
    return list((
        await db.execute(
            select(Order)
            .options(selectinload(Order.table), selectinload(Order.items).selectinload(OrderItem.options))
            .where(Order.store_id == store_id, Order.status.in_(ACTIVE_STATUSES))
            .order_by(Order.created_at.desc()).limit(FEED_LIMIT)
        )
    ).scalars().all())


async def advance(db: AsyncSession, store_id: int, order_id: int) -> None:
    """주문을 다음 단계로 (관리자 권한으로, 데모 매장 주문만)"""
    order = (
        await db.execute(select(Order).where(Order.id == order_id, Order.store_id == store_id))
    ).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="주문을 찾을 수 없습니다.")
    target = next_status(order.status)
    if target is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 끝난 주문입니다.")
    await db.rollback()  # 조회만 했던 트랜잭션을 닫고, 상태 변경은 행 잠금과 함께 다시
    await store_service.change_status_by_role(
        db, order_id, target, actor=Role.ADMIN, board=Role.ADMIN, store_id=store_id
    )


def check_order_limits(cart_items) -> None:
    total = sum(c.quantity for c in cart_items)
    if total > MAX_QUANTITY_PER_ORDER:
        raise HTTPException(
            status_code=400, detail=f"데모 매장은 한 번에 {MAX_QUANTITY_PER_ORDER}개까지 주문할 수 있습니다."
        )


# =====================================================================
# 자동 정리
# =====================================================================
async def cleanup(db: AsyncSession, store_id: int, now: datetime | None = None) -> int:
    """ORDER_TTL보다 오래된 데모 주문 삭제 (메뉴/옵션은 DB에서 함께 삭제). 화면 갱신 신호도 보낸다."""
    cutoff = (now or datetime.now(timezone.utc)) - ORDER_TTL
    rows = (
        await db.execute(
            delete(Order).where(Order.store_id == store_id, Order.created_at < cutoff)
            .returning(Order.id, Order.table_id)
        )
    ).all()
    for order_id, table_id in rows:
        await notify_order_changed(db, store_id, table_id, order_id, "DELETED")
    await db.commit()
    return len(rows)


async def cleanup_forever() -> None:
    """main.py lifespan에서 실행. 워커가 여러 개여도 같은 삭제를 반복할 뿐이라 안전하다."""
    if not enabled():
        return
    while True:
        try:
            async with AsyncSessionLocal() as db:
                store = await demo_store(db)
                if store:
                    removed = await cleanup(db, store.id)
                    if removed:
                        logger.info("데모 주문 %s건 정리", removed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("데모 주문 정리 실패")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
