"""매장 관리자 메뉴 관리 서비스 (카테고리 / 메뉴 / 옵션 그룹 / 옵션 항목 / 메뉴 상태).

모든 조회는 로그인 세션의 store_id로 범위를 제한한다.
다른 매장의 카테고리/메뉴/옵션 id를 넣으면 존재 여부도 숨기려고 404로 응답한다.
폼 값 검증 실패는 MenuError(메시지)로 올리고, 라우터가 폼을 다시 그려 보여준다.
"""
from collections.abc import Mapping

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Category, Item, OptionDetail, OptionGroup, OptionType
from app.domain.menu import ITEM_FLAGS

MAX_PRICE = 10_000_000
MAX_ORDER = 9_999


class MenuError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what}을(를) 찾을 수 없습니다.")


# =====================================================================
# 폼 값 파싱
# =====================================================================
def _text(form: Mapping, key: str, label: str, max_len: int, *, required: bool = True) -> str | None:
    value = (form.get(key) or "").strip()
    if not value:
        if required:
            raise MenuError(f"{label}을(를) 입력해 주세요.")
        return None
    if len(value) > max_len:
        raise MenuError(f"{label}은(는) {max_len}자 이하로 입력해 주세요.")
    return value


def _int(form: Mapping, key: str, label: str, *, minimum: int, maximum: int, default: int | None = None) -> int:
    raw = (form.get(key) or "").strip().replace(",", "")
    if not raw and default is not None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise MenuError(f"{label}은(는) 숫자로 입력해 주세요.") from None
    if not minimum <= value <= maximum:
        raise MenuError(f"{label}은(는) {minimum:,} ~ {maximum:,} 사이로 입력해 주세요.")
    return value


def _image_url(form: Mapping) -> str | None:
    url = _text(form, "image_url", "이미지 주소", 255, required=False)
    # javascript: 같은 스킴을 막기 위해 http(s) 또는 사이트 내부 경로만 허용
    if url and not (url.startswith(("http://", "https://")) or (url.startswith("/") and not url.startswith("//"))):
        raise MenuError("이미지 주소는 http(s):// 또는 / 로 시작해야 합니다.")
    return url


def _checked(form: Mapping, key: str) -> bool:
    return form.get(key) in ("on", "true", "1")


# =====================================================================
# 조회 (매장 범위 제한)
# =====================================================================
async def get_menu(db: AsyncSession, store_id: int) -> list[Category]:
    """관리 화면용 전체 메뉴 (숨김/품절 포함, 옵션 그룹 수 표시를 위해 옵션까지 로드)"""
    stmt = (
        select(Category)
        .options(selectinload(Category.items).selectinload(Item.option_groups).selectinload(OptionGroup.details))
        .where(Category.store_id == store_id)
        .order_by(Category.display_order.asc(), Category.id.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_category(db: AsyncSession, store_id: int, category_id: int) -> Category:
    stmt = (
        select(Category)
        .options(selectinload(Category.items))
        .where(Category.id == category_id, Category.store_id == store_id)
    )
    category = (await db.execute(stmt)).scalar_one_or_none()
    if not category:
        raise _not_found("카테고리")
    return category


async def get_item(db: AsyncSession, store_id: int, item_id: int) -> Item:
    stmt = (
        select(Item)
        .join(Category, Item.category_id == Category.id)
        .options(selectinload(Item.option_groups).selectinload(OptionGroup.details))
        .where(Item.id == item_id, Category.store_id == store_id)
        .execution_options(populate_existing=True)  # 옵션 변경 직후 다시 그릴 때 최신 관계를 읽도록
    )
    item = (await db.execute(stmt)).scalar_one_or_none()
    if not item:
        raise _not_found("메뉴")
    return item


async def get_group(db: AsyncSession, store_id: int, group_id: int) -> OptionGroup:
    stmt = (
        select(OptionGroup)
        .join(Item, OptionGroup.item_id == Item.id)
        .join(Category, Item.category_id == Category.id)
        .where(OptionGroup.id == group_id, Category.store_id == store_id)
    )
    group = (await db.execute(stmt)).scalar_one_or_none()
    if not group:
        raise _not_found("옵션 그룹")
    return group


async def get_detail(db: AsyncSession, store_id: int, detail_id: int) -> OptionDetail:
    stmt = (
        select(OptionDetail)
        .join(OptionGroup, OptionDetail.group_id == OptionGroup.id)
        .join(Item, OptionGroup.item_id == Item.id)
        .join(Category, Item.category_id == Category.id)
        .where(OptionDetail.id == detail_id, Category.store_id == store_id)
    )
    detail = (await db.execute(stmt)).scalar_one_or_none()
    if not detail:
        raise _not_found("옵션")
    return detail


async def _next_order(db: AsyncSession, column, *where) -> int:
    current = (await db.execute(select(func.max(column)).where(*where))).scalar()
    return min((current or 0) + 1, MAX_ORDER)


# =====================================================================
# 카테고리
# =====================================================================
async def create_category(db: AsyncSession, store_id: int, form: Mapping) -> Category:
    name = _text(form, "name", "카테고리 이름", 50)
    order = await _next_order(db, Category.display_order, Category.store_id == store_id)
    category = Category(store_id=store_id, name=name, display_order=order)
    db.add(category)
    await db.commit()
    return category


async def update_category(db: AsyncSession, category: Category, form: Mapping) -> None:
    category.name = _text(form, "name", "카테고리 이름", 50)
    category.display_order = _int(form, "display_order", "순서", minimum=0, maximum=MAX_ORDER)
    await db.commit()


async def delete_category(db: AsyncSession, category: Category) -> None:
    # 메뉴까지 연쇄 삭제되는 사고를 막기 위해 비어 있는 카테고리만 삭제한다
    if category.items:
        raise MenuError(f"'{category.name}'에 메뉴가 {len(category.items)}개 있습니다. 메뉴를 먼저 옮기거나 삭제해 주세요.")
    await db.delete(category)
    await db.commit()


# =====================================================================
# 메뉴
# =====================================================================
async def _apply_item_form(db: AsyncSession, store_id: int, item: Item, form: Mapping) -> None:
    category_id = _int(form, "category_id", "카테고리", minimum=1, maximum=2**31 - 1)
    await get_category(db, store_id, category_id)  # 다른 매장 카테고리로 옮기는 것 방지 (없으면 404)

    item.category_id = category_id
    item.name = _text(form, "name", "메뉴 이름", 100)
    item.price = _int(form, "price", "가격", minimum=0, maximum=MAX_PRICE)
    item.description = _text(form, "description", "설명", 500, required=False)
    item.image_url = _image_url(form)
    for flag in ITEM_FLAGS:
        setattr(item, flag, _checked(form, flag))


async def create_item(db: AsyncSession, store_id: int, form: Mapping) -> Item:
    item = Item()
    await _apply_item_form(db, store_id, item, form)
    item.display_order = await _next_order(db, Item.display_order, Item.category_id == item.category_id)
    db.add(item)
    await db.commit()
    return item


async def update_item(db: AsyncSession, store_id: int, item: Item, form: Mapping) -> None:
    await _apply_item_form(db, store_id, item, form)
    item.display_order = _int(form, "display_order", "순서", minimum=0, maximum=MAX_ORDER)
    await db.commit()


async def delete_item(db: AsyncSession, item: Item) -> None:
    # 지난 주문은 메뉴 이름/가격을 스냅샷으로 갖고 있어(OrderItem) 메뉴를 지워도 주문 내역은 남는다
    await db.delete(item)
    await db.commit()


async def toggle_flag(db: AsyncSession, item: Item, flag: str) -> None:
    if flag not in ITEM_FLAGS:
        raise _not_found("메뉴 상태")
    setattr(item, flag, not getattr(item, flag))
    await db.commit()


# =====================================================================
# 옵션 그룹 / 옵션 항목
# =====================================================================
def _apply_group_form(group: OptionGroup, form: Mapping) -> None:
    group.name = _text(form, "name", "옵션 그룹 이름", 50)
    try:
        group.option_type = OptionType(form.get("option_type") or "")
    except ValueError:
        raise MenuError("옵션 종류를 선택해 주세요.") from None
    group.is_required = _checked(form, "is_required")
    # 단일 선택형은 항상 1개, 추가형은 최대 선택 수를 받는다
    group.max_select = (
        1 if group.option_type is OptionType.SELECT
        else _int(form, "max_select", "최대 선택 수", minimum=1, maximum=99, default=1)
    )


async def create_group(db: AsyncSession, item: Item, form: Mapping) -> None:
    group = OptionGroup(item_id=item.id)
    _apply_group_form(group, form)
    db.add(group)
    await db.commit()


async def update_group(db: AsyncSession, group: OptionGroup, form: Mapping) -> None:
    _apply_group_form(group, form)
    await db.commit()


async def delete_group(db: AsyncSession, group: OptionGroup) -> None:
    await db.delete(group)
    await db.commit()


def _apply_detail_form(detail: OptionDetail, form: Mapping) -> None:
    detail.name = _text(form, "name", "옵션 이름", 50)
    detail.additional_price = _int(form, "additional_price", "추가 금액", minimum=0, maximum=MAX_PRICE, default=0)


async def create_detail(db: AsyncSession, group: OptionGroup, form: Mapping) -> None:
    detail = OptionDetail(group_id=group.id)
    _apply_detail_form(detail, form)
    detail.display_order = await _next_order(db, OptionDetail.display_order, OptionDetail.group_id == group.id)
    db.add(detail)
    await db.commit()


async def update_detail(db: AsyncSession, detail: OptionDetail, form: Mapping) -> None:
    _apply_detail_form(detail, form)
    detail.display_order = _int(form, "display_order", "순서", minimum=0, maximum=MAX_ORDER)
    await db.commit()


async def delete_detail(db: AsyncSession, detail: OptionDetail) -> None:
    await db.delete(detail)
    await db.commit()
