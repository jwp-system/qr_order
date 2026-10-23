# app/routers/super_admin.py
import secrets
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app2.core.config import settings
from app2.core.security import hash_pin
from app2.database.models import LicenseStatus, Order, Store, Table
from app2.database.session import get_db

router = APIRouter(prefix="/super", tags=["Super Admin"])
templates = Jinja2Templates(directory=settings.TEMPLATES_DIR)


@router.get("/dashboard", response_class=HTMLResponse)
async def super_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """시스템 관리자 대시보드: 전체 지표 요약 및 매장 목록"""
    # 1. 전체 매장 및 테이블 정보 로드
    stores_stmt = (
        select(Store)
        .options(selectinload(Store.tables))
        .order_by(Store.created_at.desc())
    )
    stores_res = await db.execute(stores_stmt)
    stores = stores_res.scalars().all()

    # 2. 통계 지표 산출
    total_stores = len(stores)
    active_stores = sum(1 for s in stores if s.license_status == LicenseStatus.ACTIVE)
    trial_stores = sum(1 for s in stores if s.license_status == LicenseStatus.TRIAL)

    # 금일 누적 주문량 및 매출 계산
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    order_stats_stmt = (
        select(
            func.count(Order.id).label("total_orders"),
            func.coalesce(func.sum(Order.total_price), 0).label("total_revenue")
        )
        .where(Order.created_at >= today_start)
    )
    stats_res = await db.execute(order_stats_stmt)
    stats_row = stats_res.one()

    return templates.TemplateResponse(
        request=request,
        name=
        "super_admin/dashboard.html",
        context={
            "stores": stores,
            "metrics": {
                "total_stores": total_stores,
                "active_stores": active_stores,
                "trial_stores": trial_stores,
                "today_orders": stats_row.total_orders,
                "today_revenue": stats_row.total_revenue
            },
            "license_statuses": LicenseStatus
        }
    )


@router.post("/stores", response_class=HTMLResponse)
async def create_store(
    request: Request,
    name: str = Form(...),
    initial_tables: int = Form(5),
    license_status: LicenseStatus = Form(LicenseStatus.TRIAL),
    db: AsyncSession = Depends(get_db)
):
    """신규 식당 승인 및 테넌트 서비스 코드 발급"""
    # 1. 고유 서비스 코드 생성 (예: STORE-A1B2C3D4)
    service_code = f"STORE-{secrets.token_hex(4).upper()}"

    # 2. 기본 PIN 설정 (관리자: 1234, 주방: 0000, 홀: 1111)
    new_store = Store(
        name=name,
        service_code=service_code,
        license_status=license_status,
        admin_pin=hash_pin("1234"),
        kitchen_pin=hash_pin("0000"),
        hall_pin=hash_pin("1111")
    )
    db.add(new_store)
    await db.flush()

    # 3. 요청된 수만큼 기본 테이블 자동 생성
    for i in range(1, initial_tables + 1):
        table_token = f"TOKEN_{new_store.id}_{i}_{secrets.token_urlsafe(8)}"
        table = Table(
            store_id=new_store.id,
            table_number=i,
            qr_token=table_token
        )
        db.add(table)

    await db.commit()
    await db.refresh(new_store)

    # 매장 목록 상단에 새로 등록된 행 프래그먼트 반환 (HTMX hx-swap="afterbegin")
    return templates.TemplateResponse(
        request=request,
        name=
        "super_admin/fragments/store_row.html",
        context={
            "store": new_store,
            "license_statuses": LicenseStatus
        }
    )


@router.post("/stores/{store_id}/license", response_class=HTMLResponse)
async def update_store_license(
    request: Request,
    store_id: int,
    status: LicenseStatus = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """라이선스 상태 실시간 변경 (TRIAL <-> ACTIVE <-> EXPIRED)"""
    stmt = (
        select(Store)
        .options(selectinload(Store.tables))
        .where(Store.id == store_id)
    )
    res = await db.execute(stmt)
    store = res.scalar_one_or_none()
    if not store:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다.")

    store.license_status = status
    await db.commit()
    await db.refresh(store)

    # 수정된 행만 부분 교체 반환
    return templates.TemplateResponse(
        request=request,
        name="super_admin/fragments/store_row.html",
        context={
            "store": store,
            "license_statuses": LicenseStatus
        }
    )


@router.post("/stores/{store_id}/reset-pins", response_class=HTMLResponse)
async def reset_store_pins(
    request: Request,
    store_id: int,
    db: AsyncSession = Depends(get_db)
):
    """매장 운영 핀코드 기본값(1234/0000/1111)으로 리셋"""
    stmt = (
        select(Store)
        .options(selectinload(Store.tables))
        .where(Store.id == store_id)
    )
    res = await db.execute(stmt)
    store = res.scalar_one_or_none()
    if not store:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다.")

    store.admin_pin = hash_pin("1234")
    store.kitchen_pin = hash_pin("0000")
    store.hall_pin = hash_pin("1111")
    await db.commit()
    await db.refresh(store)

    return templates.TemplateResponse(
        request=request,
        name=
        "super_admin/fragments/store_row.html",
        context={
            "store": store,
            "license_statuses": LicenseStatus,
            "alert_msg": "PIN이 기본값으로 초기화되었습니다."
        }
    )