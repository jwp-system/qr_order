"""실시간 이벤트 (SSE). 보드 화면이 구독하고, 변경 신호를 받으면 HTMX가 목록을 다시 가져온다."""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app2.core.events import broker

router = APIRouter(tags=["Events"])


@router.get("/events/stores/{store_id}")
async def store_events(store_id: int):
    """매장 단위 주문 변경 스트림 (text/event-stream).
    TODO: 인증 연결 시 세션의 store_id와 일치하는지 검사."""
    return StreamingResponse(
        broker.subscribe(store_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx 버퍼링 방지
        },
    )