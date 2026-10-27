# app/routers/super_admin.py
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.clock import business_today
from app.core.security import hash_pin
from app.core.templating import templates
from app.database.models import LicenseStatus, Order, Store, Table
from app.database.session import get_db
from app.domain.roles import Role
from app.services.auth_service import DEFAULT_PINS
from app.services import inquiry_service
from app.services.table_service import new_qr_token

router = APIRouter(prefix="/super", tags=["Super Admin"])


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
        name= "super_admin/dashboard.html",
        context={
            "stores": stores,
            "metrics": {
                "total_stores": total_stores,
                "active_stores": active_stores,
                "trial_stores": trial_stores,
                "today_orders": stats_row.total_orders,
                "today_revenue": stats_row.total_revenue
            },
            "license_statuses": LicenseStatus,
            "unanswered": await inquiry_service.unanswered_count(db),
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
        admin_pin=hash_pin(DEFAULT_PINS[Role.ADMIN]),
        kitchen_pin=hash_pin(DEFAULT_PINS[Role.KITCHEN]),
        hall_pin=hash_pin(DEFAULT_PINS[Role.HALL])
    )
    db.add(new_store)
    await db.flush()

    # 3. 요청된 수만큼 기본 테이블 자동 생성
    for i in range(1, initial_tables + 1):
        table = Table(
            store_id=new_store.id,
            table_number=i,
            qr_token=new_qr_token()
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
    """라이선스 상태 실시간 변경 (TRIAL <-> ACTIVE <-> EXPIRED).
    EXPIRED로 바꾼 날(영업일)을 종료일로 기록한다. 이날부터 1개월간 관리자 조회 전용 (domain/license.py)."""
    stmt = (
        select(Store)
        .options(selectinload(Store.tables))
        .where(Store.id == store_id)
    )
    res = await db.execute(stmt)
    store = res.scalar_one_or_none()
    if not store:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다.")

    if status is LicenseStatus.EXPIRED:
        if store.license_status is not LicenseStatus.EXPIRED:  # 이미 종료된 매장은 종료일 유지
            store.expired_on = business_today()
    else:
        store.expired_on = None
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
    """매장 운영 핀코드 기본값(1234/0000/1111)으로 리셋. 다음 관리자 로그인 시 PIN 변경이 다시 강제된다."""
    stmt = (
        select(Store)
        .options(selectinload(Store.tables))
        .where(Store.id == store_id)
    )
    res = await db.execute(stmt)
    store = res.scalar_one_or_none()
    if not store:
        raise HTTPException(status_code=404, detail="매장을 찾을 수 없습니다.")

    store.admin_pin = hash_pin(DEFAULT_PINS[Role.ADMIN])
    store.kitchen_pin = hash_pin(DEFAULT_PINS[Role.KITCHEN])
    store.hall_pin = hash_pin(DEFAULT_PINS[Role.HALL])
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

# =====================================================================
# 문의 게시판 관리 (/super/inquiries)
# =====================================================================
@router.get("/inquiries", response_class=HTMLResponse)
async def super_inquiries(
    request: Request,
    page: int = 1,
    show_all: bool = Query(False, alias="all"),  # 기본은 답변 대기만
    db: AsyncSession = Depends(get_db),
):
    return templates.TemplateResponse(
        request=request,
        name="super_admin/inquiries.html",
        context={
            "result": await inquiry_service.list_page(db, page, unanswered=not show_all),
            "only_open": not show_all,
            "unanswered": await inquiry_service.unanswered_count(db),
        },
    )


@router.post("/inquiries/{inquiry_id}/answer")
async def super_answer_inquiry(
    inquiry_id: int,
    answer: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """답변 저장 후 전체 목록의 해당 문의로 (답변 대기 목록에서는 사라지므로)"""
    inquiry = await inquiry_service.get(db, inquiry_id)
    try:
        await inquiry_service.answer(db, inquiry, answer)
    except inquiry_service.InquiryError as e:
        raise HTTPException(status_code=422, detail=e.message) from None
    return RedirectResponse(url=f"/super/inquiries?all=1#q-{inquiry_id}", status_code=303)


@router.post("/inquiries/{inquiry_id}/delete")
async def super_delete_inquiry(inquiry_id: int, db: AsyncSession = Depends(get_db)):
    await inquiry_service.delete(db, await inquiry_service.get(db, inquiry_id))
    return RedirectResponse(url="/super/inquiries?all=1", status_code=303)
