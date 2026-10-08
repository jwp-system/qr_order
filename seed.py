# seed.py
import asyncio
import hashlib
from app.database.session import AsyncSessionLocal
from app.database.models import (
    Store, Table, Category, Item, OptionGroup, OptionDetail,
    OptionType, LicenseStatus
)

def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()

async def populate_seed_data():
    async with AsyncSessionLocal() as session:
        # 1. 매장 생성
        store = Store(
            name="명품 토종 순대국 본점",
            service_code="STORE-SEOUL-001",
            license_status=LicenseStatus.ACTIVE,
            admin_pin=hash_pin("1234"),
            kitchen_pin=hash_pin("0000"),
            hall_pin=hash_pin("1111")
        )
        session.add(store)
        await session.flush()

        # 2. 테이블 3개 생성 (토큰은 임의 문자열로 테스트)
        tables = [
            Table(store_id=store.id, table_number=1, qr_token="TOKEN_TABLE_01"),
            Table(store_id=store.id, table_number=2, qr_token="TOKEN_TABLE_02"),
            Table(store_id=store.id, table_number=3, qr_token="TOKEN_TABLE_03"),
        ]
        session.add_all(tables)

        # 3. 카테고리 생성
        cat_main = Category(store_id=store.id, name="식사류", display_order=1)
        cat_side = Category(store_id=store.id, name="안주/추가", display_order=2)
        session.add_all([cat_main, cat_side])
        await session.flush()

        # 4. 메뉴 및 옵션 구성 (순대국)
        item_soondae = Item(
            category_id=cat_main.id,
            name="얼큰 토종 순대국",
            description="진하게 우려낸 사골 육수와 부드러운 머릿고기",
            price=10000,
            display_order=1
        )
        session.add(item_soondae)
        await session.flush()

        # 옵션 1: 맵기 선택 (단일 선택 필수형)
        opt_group_spicy = OptionGroup(
            item_id=item_soondae.id,
            name="맵기 조절",
            option_type=OptionType.SELECT,
            is_required=True,
            max_select=1
        )
        # 옵션 2: 추가 토핑 (다중 선택형)
        opt_group_add = OptionGroup(
            item_id=item_soondae.id,
            name="추가 선택",
            option_type=OptionType.ADDITIONAL,
            is_required=False,
            max_select=3
        )
        session.add_all([opt_group_spicy, opt_group_add])
        await session.flush()

        # 옵션 세부 항목
        details = [
            OptionDetail(group_id=opt_group_spicy.id, name="보통맛 (기본)", additional_price=0, display_order=1),
            OptionDetail(group_id=opt_group_spicy.id, name="매운맛", additional_price=0, display_order=2),
            OptionDetail(group_id=opt_group_spicy.id, name="아주 매운맛", additional_price=500, display_order=3),
            OptionDetail(group_id=opt_group_add.id, name="공기밥 추가", additional_price=1000, display_order=1),
            OptionDetail(group_id=opt_group_add.id, name="순대 추가 (5개)", additional_price=3000, display_order=2),
        ]
        session.add_all(details)

        await session.commit()
        print("Seed data successfully injected!")

if __name__ == "__main__":
    asyncio.run(populate_seed_data())