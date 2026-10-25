"""실시간 이벤트 (SSE).
- 직원 보드: 변경 신호를 받으면 HTMX가 목록을 다시 가져온다.
- 고객 화면: 자기 테이블의 변경 신호를 받으면 헤더(주문 건수)와 열린 주문 현황 모달을 갱신한다."""
from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse

from app2.core.events import broker, store_scope, table_scope
from app2.database.session import AsyncSessionLocal
from app2.services import client_service

router = APIRouter(tags=["Events"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # nginx 버퍼링 방지
}


def _stream(scope) -> StreamingResponse:
    return StreamingResponse(
        broker.subscribe(scope), media_type="text/event-stream", headers=_SSE_HEADERS
    )


@router.get("/events/stores/{store_id}")
async def store_events(store_id: int):
    """매장 단위 주문 변경 스트림 (직원 보드용).
    TODO: 인증 연결 시 세션의 store_id와 일치하는지 검사."""
    return _stream(store_scope(store_id))


@router.get("/events/tables/{qr_token}")
async def table_events(qr_token: str):
    """테이블 단위 주문 변경 스트림 (고객 화면용). QR 토큰이 곧 접근 권한이다."""
    # 스트림이 열려 있는 동안 DB 커넥션을 쥐지 않도록, 세션은 조회 직후 닫는다
    # (Depends(get_db)를 쓰면 응답이 끝날 때까지 커넥션이 풀에서 빠져 있게 된다)
    async with AsyncSessionLocal() as db:
        table = await client_service.get_active_table_by_token(
            db, qr_token, not_found_status=status.HTTP_404_NOT_FOUND
        )
        table_id = table.id
    return _stream(table_scope(table_id))
