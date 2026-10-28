"""서비스 소개 페이지 (/) — 로그인 불필요.

'주문 입력 테스트' 데모 섹션은 데모 매장/QR이 설정돼 있고 열 수 있을 때만 보여준다 (services/demo_service.py).
아니면 '데모 요청'(문의 게시판) 버튼만 보여준다. 데모 API는 routers/demo.py.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import templates
from app.database.session import get_db
from app.services import demo_service, inquiry_service

router = APIRouter(tags=["Landing"])


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing(request: Request, db: AsyncSession = Depends(get_db)):
    recent = await inquiry_service.list_page(db, 1, public=True)
    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={"demo": await demo_service.landing_context(request, db), "recent_inquiries": recent.items[:5]},
    )
