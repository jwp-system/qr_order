"""랜딩 '주문 입력 테스트' 공개 데모 API — 로그인 불필요, 데모 매장(DEMO_SERVICE_CODE) 주문만 다룬다.

  GET  /demo/qr.png                      데모 테이블 QR
  GET  /demo/feed                        공개 주방 보드 (진행 중 주문 카드)
  GET  /demo/events                      데모 매장 주문 변경 SSE (보드 자동 갱신)
  POST /demo/orders/{id}/advance         다음 단계로 (접수 -> 조리 시작 -> 조리 완료 -> 서빙 완료 -> 마감)

데모 주문 자동 정리/수량 제한은 services/demo_service.py, 주문 횟수 제한은 routers/client.py.
"""
import math

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import broker, store_scope
from app.core.rate_limit import FailureLimiter
from app.core.templating import templates
from app.database.session import AsyncSessionLocal, get_db
from app.services import demo_service as svc
from app.services.table_service import qr_png

router = APIRouter(prefix="/demo", tags=["Demo"])

# 같은 IP에서 10분에 60번까지 상태 변경 (장난 방지)
_advance_limiter = FailureLimiter(max_failures=60, window_seconds=600, lock_seconds=600)


def _feed(request: Request, orders, *, error: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="demo/feed.html",
        context={"orders": orders, "next_action": svc.NEXT_ACTION, "error": error},
    )


@router.get("/qr.png", include_in_schema=False)
async def demo_qr(request: Request, db: AsyncSession = Depends(get_db)):
    demo = await svc.landing_context(request, db)
    if not demo:
        return Response(status_code=404)
    return Response(content=qr_png(demo["order_url"]), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/feed", response_class=HTMLResponse)
async def demo_feed(request: Request, db: AsyncSession = Depends(get_db)):
    store = await svc.require_demo_store(db)
    return _feed(request, await svc.feed_orders(db, store.id))


@router.get("/events")
async def demo_events():
    # 스트림이 열려 있는 동안 DB 커넥션을 쥐지 않도록 세션은 조회 직후 닫는다 (routers/events.py와 같은 이유)
    async with AsyncSessionLocal() as db:
        store_id = (await svc.require_demo_store(db)).id
    return StreamingResponse(
        broker.subscribe(store_scope(store_id)), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/orders/{order_id}/advance", response_class=HTMLResponse)
async def demo_advance(request: Request, order_id: int, db: AsyncSession = Depends(get_db)):
    store_id = (await svc.require_demo_store(db)).id  # advance가 롤백하면 ORM 객체가 만료되므로 값으로 들고 있는다
    ip = request.client.host if request.client else "unknown"
    wait = _advance_limiter.retry_after(ip)
    if wait:
        return _feed(request, await svc.feed_orders(db, store_id), error=f"잠시 후 다시 눌러 주세요 ({math.ceil(wait / 60)}분).")
    _advance_limiter.record_failure(ip)  # 누른 횟수로 센다
    try:
        await svc.advance(db, store_id, order_id)
    except HTTPException as e:  # 다른 방문자가 먼저 눌렀거나 정리된 주문: 안내와 함께 목록만 새로
        await db.rollback()
        return _feed(request, await svc.feed_orders(db, store_id), error=str(e.detail))
    return _feed(request, await svc.feed_orders(db, store_id))
