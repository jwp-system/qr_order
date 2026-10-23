"""고객용 서비스 (QR 테이블 확인, 메뉴 조회, 주문 생성)"""
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app2.core.events import notify_order_changed
from app2.database.models import (
    Category, Item, OptionGroup,
    Order, OrderItem, OrderItemOption, OrderStatus, Table,
)
from app2.domain.order_status import CUSTOMER_VISIBLE_STATUSES


# =====================================================================
# 테이블 / 메뉴 조회
# =====================================================================
async def get_active_table_by_token(
    db: AsyncSession,
    qr_token: str,
    *,
    load_store: bool = False,
    not_found_status: int = status.HTTP_400_BAD_REQUEST,
) -> Table:
    """QR 토큰으로 활성 테이블 조회. 없으면 not_found_status로 예외."""
    stmt = select(Table).where(Table.qr_token == qr_token, Table.is_active.is_(True))
    if load_store:
        stmt = stmt.options(selectinload(Table.store))
    table = (await db.execute(stmt)).scalar_one_or_none()

    if not table:
        detail = (
            "유효하지 않거나 비활성화된 테이블 QR입니다."
            if not_found_status == status.HTTP_404_NOT_FOUND
            else "유효하지 않은 테이블입니다."
        )
        raise HTTPException(status_code=not_found_status, detail=detail)
    return table


async def get_menu_categories(db: AsyncSession, store_id: int) -> list[Category]:
    stmt = (
        select(Category)
        .options(selectinload(Category.items))
        .where(Category.store_id == store_id)
        .order_by(Category.display_order.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_item_with_options(db: AsyncSession, item_id: int) -> Item:
    stmt = (
        select(Item)
        .options(selectinload(Item.option_groups).selectinload(OptionGroup.details))
        .where(Item.id == item_id)
    )
    item = (await db.execute(stmt)).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="해당 메뉴를 찾을 수 없습니다.")
    return item


def build_option_modal_data(item: Item) -> dict:
    """옵션 모달용 JS 전달 데이터 (프레젠테이션 매핑)"""
    return {
        "itemId": item.id,
        "itemName": item.name,
        "basePrice": item.price,
        "option_groups": [
            {
                "groupId": g.id,
                "groupName": g.name or "",
                "isRequired": g.is_required,
            }
            for g in item.option_groups
        ],
    }


# =====================================================================
# 고객 주문 조회
# =====================================================================
async def count_table_active_orders(db: AsyncSession, table_id: int) -> int:
    stmt = select(func.count(Order.id)).where(
        Order.table_id == table_id,
        Order.status.in_(CUSTOMER_VISIBLE_STATUSES),
    )
    return (await db.execute(stmt)).scalar() or 0


async def get_table_orders(db: AsyncSession, table_id: int) -> list[Order]:
    stmt = (
        select(Order)
        .options(selectinload(Order.items).selectinload(OrderItem.options))
        .where(Order.table_id == table_id, Order.status.in_(CUSTOMER_VISIBLE_STATUSES))
        .order_by(Order.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


# =====================================================================
# 화면용 컨텍스트 (라우터를 얇게 유지하기 위한 조합 함수)
# =====================================================================
async def get_active_order_count(db, qr_token:str) -> dict:
    """/order, /order/{qr_token} 공통: 테이블/매장/카테고리/진행중 주문 수"""
    table = await get_active_table_by_token(
        db, qr_token, load_store=True, not_found_status=status.HTTP_404_NOT_FOUND
    )
    return {
        "qr_token": qr_token,
        "active_order_count": await count_table_active_orders(db, table.id),
    }

async def get_menu_context(db: AsyncSession, qr_token: str) -> dict:
    """/order, /order/{qr_token} 공통: 테이블/매장/카테고리/진행중 주문 수"""
    table = await get_active_table_by_token(
        db, qr_token, load_store=True, not_found_status=status.HTTP_404_NOT_FOUND
    )
    store = table.store
    return {
        "qr_token": qr_token,
        "store": store,
        "table": table,
        "categories": await get_menu_categories(db, store.id),
        "active_order_count": await count_table_active_orders(db, table.id),
    }


async def get_order_status_context(db: AsyncSession, qr_token: str) -> dict:
    """주문 현황 모달용"""
    table = await get_active_table_by_token(db, qr_token)
    orders = await get_table_orders(db, table.id)
    return {
        "table": table,
        "orders": orders,
        "total_table_amount": sum(o.total_price for o in orders),
    }


async def get_item_modal_context(db: AsyncSession, item_id: int) -> dict:
    """옵션 선택 모달용"""
    item = await get_item_with_options(db, item_id)
    return {"item": item, "option_data": build_option_modal_data(item)}


# =====================================================================
# 주문 생성
# =====================================================================
def _line_total(unit_price: int, options, quantity: int) -> int:
    return (unit_price + sum(o.additional_price for o in options)) * quantity


async def _load_cart_items(
    db: AsyncSession, cart_items, store_id: int
) -> dict[int, Item]:
    """장바구니의 메뉴를 일괄 조회 (N+1 방지).
    해당 매장의 메뉴만 허용한다. 없거나 다른 매장 메뉴면 400."""
    item_ids = {c.item_id for c in cart_items}
    stmt = (
        select(Item)
        .join(Category, Item.category_id == Category.id)
        .options(selectinload(Item.option_groups).selectinload(OptionGroup.details))
        .where(Item.id.in_(item_ids), Category.store_id == store_id)
    )
    rows = (await db.execute(stmt)).scalars().all()
    items_by_id = {i.id: i for i in rows}

    if item_ids - items_by_id.keys():
        raise HTTPException(status_code=400, detail="존재하지 않는 메뉴가 포함되어 있습니다.")
    return items_by_id


def _option_price_map(item: Item) -> dict[str, set[int]]:
    """옵션명 -> 허용 가격 집합 (그룹이 달라도 이름이 같을 수 있어 set)"""
    prices: dict[str, set[int]] = {}
    for group in item.option_groups:
        for detail in group.details:
            prices.setdefault(detail.name, set()).add(detail.additional_price)
    return prices


def _validate_cart(cart_items, items_by_id: dict[int, Item]) -> int:
    """품절/가격/옵션 검증 후 서버 기준 총액 반환.

    NOTE: 필수 옵션(is_required)과 max_select는 payload에 옵션 id/그룹 정보가 없어
    이름만으로는 확정할 수 없어 검증하지 않는다. 클라이언트가 option_id를 보내도록
    바꾸면 검증 가능.
    """
    total_price = 0
    for c in cart_items:
        db_item = items_by_id[c.item_id]
        if db_item.is_sold_out:
            raise HTTPException(
                status_code=409, detail=f"'{db_item.name}'은(는) 품절되었습니다."
            )
        if c.base_price != db_item.price:
            raise HTTPException(
                status_code=409,
                detail=f"'{db_item.name}' 가격이 변경되었습니다. 다시 담아주세요.",
            )

        option_prices = _option_price_map(db_item)
        for o in c.options:
            allowed = option_prices.get(o.name)
            if allowed is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{db_item.name}'에 없는 옵션입니다: {o.name}",
                )
            if o.additional_price not in allowed:
                raise HTTPException(
                    status_code=409,
                    detail=f"'{o.name}' 옵션 가격이 변경되었습니다. 다시 담아주세요.",
                )

        expected = _line_total(db_item.price, c.options, c.quantity)
        if c.subtotal_price != expected:
            raise HTTPException(status_code=400, detail="주문 금액이 올바르지 않습니다.")
        total_price += expected
    return total_price


async def _save_order(
    db: AsyncSession,
    table: Table,
    cart_items,
    items_by_id: dict[int, Item],
    special_instruction: str | None,
    total_price: int,
) -> Order:
    order = Order(
        store_id=table.store_id,
        table_id=table.id,
        status=OrderStatus.ORDERED,
        special_instruction=special_instruction,
        total_price=total_price,
    )
    db.add(order)
    await db.flush()

    for c in cart_items:
        db_item = items_by_id[c.item_id]
        order_item = OrderItem(
            order_id=order.id,
            item_id=db_item.id,
            item_name=db_item.name,  # 스냅샷은 DB 값 사용
            base_price=db_item.price,
            quantity=c.quantity,
            subtotal_price=_line_total(db_item.price, c.options, c.quantity),
        )
        db.add(order_item)
        await db.flush()

        db.add_all(
            OrderItemOption(
                order_item_id=order_item.id,
                option_name=o.name,
                additional_price=o.additional_price,
            )
            for o in c.options
        )
    return order


async def create_order(
    db: AsyncSession,
    table: Table,
    cart_items,
    special_instruction: str | None,
) -> Order:
    """장바구니 -> 주문 스냅샷 저장 (한 트랜잭션).
    가격/메뉴명은 클라이언트 값을 믿지 않고 DB 기준으로 검증한다."""
    if not cart_items:
        raise HTTPException(status_code=400, detail="장바구니가 비어 있습니다.")

    items_by_id = await _load_cart_items(db, cart_items, table.store_id)
    total_price = _validate_cart(cart_items, items_by_id)

    try:
        order = await _save_order(
            db, table, cart_items, items_by_id, special_instruction, total_price
        )
        await notify_order_changed(db, table.store_id, order.id, order.status)  # 커밋 후 전달
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(status_code=500, detail="주문 저장 중 오류가 발생했습니다.")

    await db.refresh(order)
    return order