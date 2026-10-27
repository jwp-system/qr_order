# app/database/models.py
import enum
from typing import List, Optional
from sqlalchemy import (
    String, Integer, Boolean, ForeignKey, Text, Enum, UniqueConstraint, Index, false
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin
# --- Enums ---
class OrderStatus(str, enum.Enum):
    ORDERED = "ORDERED"       # 고객 주문 직후 (관리자 승인 대기)
    ACCEPTED = "ACCEPTED"     # 관리자 접수 확인 (주방 조리 대기)
    COOKING = "COOKING"       # 주방 조리 중
    COOKED = "COOKED"         # 주방 조리 완료 (홀 서빙 대기)
    SERVED = "SERVED"         # 홀 서빙 완료 (결제/최종 대기)
    COMPLETED = "COMPLETED"   # 관리자 최종 마감/완료
    CANCELLED = "CANCELLED"   # 주문 취소/반려

class OptionType(str, enum.Enum):
    SELECT = "SELECT"         # 단일 선택형 (라디오: 맵기 등)
    ADDITIONAL = "ADDITIONAL" # 다중 추가형 (체크박스/카운터: 공기밥 추가 등)

class TableStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"   # 주문 가능
    PAUSED = "PAUSED"         # 주문 중지: QR로 메뉴/주문 내역은 보이지만 새 주문 불가 (브레이크 타임, 예약석 등)
    DISABLED = "DISABLED"     # 사용 안 함: QR 접속 불가

class LicenseStatus(str, enum.Enum):
    TRIAL = "TRIAL"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"

# --- Models ---
class Store(Base, TimestampMixin):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    service_code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    license_status: Mapped[LicenseStatus] = mapped_column(
        Enum(LicenseStatus), default=LicenseStatus.TRIAL, nullable=False
    )
    admin_pin: Mapped[str] = mapped_column(String(64), nullable=False)   # 해시 저장
    kitchen_pin: Mapped[str] = mapped_column(String(64), nullable=False) # 해시 저장
    hall_pin: Mapped[str] = mapped_column(String(64), nullable=False)    # 해시 저장

    tables: Mapped[List["Table"]] = relationship(back_populates="store", cascade="all, delete-orphan")
    categories: Mapped[List["Category"]] = relationship(back_populates="store", cascade="all, delete-orphan")
    orders: Mapped[List["Order"]] = relationship(back_populates="store")

class Table(Base, TimestampMixin):
    __tablename__ = "tables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), nullable=False)
    table_number: Mapped[int] = mapped_column(Integer, nullable=False)
    qr_token: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    status: Mapped[TableStatus] = mapped_column(
        Enum(TableStatus), default=TableStatus.AVAILABLE, server_default=TableStatus.AVAILABLE.value, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("store_id", "table_number", name="uq_store_table_number"),
    )

    store: Mapped["Store"] = relationship(back_populates="tables")
    orders: Mapped[List["Order"]] = relationship(back_populates="table")

class Category(Base, TimestampMixin):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    store: Mapped["Store"] = relationship(back_populates="categories")
    items: Mapped[List["Item"]] = relationship(
        back_populates="category", 
        cascade="all, delete-orphan",
        order_by="Item.display_order.asc(), Item.name.asc()"
    )

class Item(Base, TimestampMixin):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # 메뉴 상태 (domain/menu.py ITEM_FLAGS)
    is_sold_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # 품절: 표시는 하되 주문 불가
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)  # 숨김: 고객 메뉴에서 제외
    is_popular: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    is_new: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    is_recommended: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 카테고리 안 정렬 (작을수록 위)

    category: Mapped["Category"] = relationship(back_populates="items")
    option_groups: Mapped[List["OptionGroup"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="OptionGroup.id"
    )

class OptionGroup(Base, TimestampMixin):
    __tablename__ = "option_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False) # 예: "맵기 선택", "추가 토핑"
    option_type: Mapped[OptionType] = mapped_column(Enum(OptionType), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_select: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    item: Mapped["Item"] = relationship(back_populates="option_groups")
    details: Mapped[List["OptionDetail"]] = relationship(
        back_populates="group", cascade="all, delete-orphan",
        order_by="OptionDetail.display_order.asc(), OptionDetail.id.asc()",
    )

class OptionDetail(Base, TimestampMixin):
    __tablename__ = "option_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("option_groups.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False) # 예: "매운맛", "공기밥 추가"
    additional_price: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    group: Mapped["OptionGroup"] = relationship(back_populates="details")

class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), nullable=False, index=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("tables.id"), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.ORDERED, nullable=False, index=True
    )
    special_instruction: Mapped[Optional[str]] = mapped_column(String(100), nullable=True) # "오이 빼주세요"
    total_price: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_orders_store_status", "store_id", "status"),
    )

    store: Mapped["Store"] = relationship(back_populates="orders")
    table: Mapped["Table"] = relationship(back_populates="orders")
    items: Mapped[List["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")

class OrderItem(Base, TimestampMixin):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    item_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True) # 원본 ID 참조용 (삭제 가능)
    item_name: Mapped[str] = mapped_column(String(100), nullable=False)    # 스냅샷
    base_price: Mapped[int] = mapped_column(Integer, nullable=False)      # 스냅샷
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    subtotal_price: Mapped[int] = mapped_column(Integer, nullable=False)  # (단가+옵션)*수량

    order: Mapped["Order"] = relationship(back_populates="items")
    options: Mapped[List["OrderItemOption"]] = relationship(back_populates="order_item", cascade="all, delete-orphan")

class OrderItemOption(Base, TimestampMixin):
    __tablename__ = "order_item_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False)
    option_name: Mapped[str] = mapped_column(String(50), nullable=False)      # 스냅샷
    additional_price: Mapped[int] = mapped_column(Integer, default=0, nullable=False) # 스냅샷
    order_item: Mapped["OrderItem"] = relationship(back_populates="options")