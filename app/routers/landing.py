"""서비스 소개 페이지 (/) 와 데모 QR (/demo/qr.png) — 로그인 불필요.

데모는 DEMO_QR_TOKEN이 실제로 열리는 테이블('사용 안 함' 아님)을 가리킬 때만 보여준다.
설정이 없거나 잘못됐으면 랜딩은 '데모 요청'(메일) 버튼만 보여준다.
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.templating import templates
from app.core.urls import order_url
from app.database.session import get_db
from app.services import client_service, inquiry_service
from app.services.table_service import qr_png

router = APIRouter(tags=["Landing"])


async def _demo(request: Request, db: AsyncSession) -> dict | None:
    """데모 정보. 데모 테이블이 없거나 꺼져 있으면 None"""
    if not (settings.DEMO_QR_TOKEN and settings.DEMO_SERVICE_CODE):
        return None
    try:
        await client_service.get_active_table_by_token(db, settings.DEMO_QR_TOKEN)
    except HTTPException:
        return None
    code = quote(settings.DEMO_SERVICE_CODE, safe="")
    return {
        "order_url": order_url(request, settings.DEMO_QR_TOKEN),
        "kitchen_url": f"/login?role=kitchen&code={code}",
        "kitchen_pin": settings.DEMO_KITCHEN_PIN,
    }


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing(request: Request, db: AsyncSession = Depends(get_db)):
    recent = await inquiry_service.list_page(db, 1, public=True)
    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={"demo": await _demo(request, db), "recent_inquiries": recent.items[:5]},
    )


@router.get("/demo/qr.png", include_in_schema=False)
async def demo_qr(request: Request, db: AsyncSession = Depends(get_db)):
    demo = await _demo(request, db)
    if not demo:
        raise HTTPException(status_code=404, detail="데모가 설정되어 있지 않습니다.")
    return Response(content=qr_png(demo["order_url"]), media_type="image/png", headers={"Cache-Control": "no-store"})
