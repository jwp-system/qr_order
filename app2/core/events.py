"""주문 변경 실시간 알림: PostgreSQL LISTEN/NOTIFY -> 프로세스 내 팬아웃 -> SSE.

흐름
  1. 서비스가 주문을 생성/변경하면 같은 트랜잭션 안에서 notify_order_changed() 호출
     (pg_notify는 커밋될 때만 전달되고, 롤백되면 전달되지 않는다)
  2. 각 워커의 broker가 LISTEN 연결로 알림을 받아 구독자들의 큐에 넣는다
     - 매장 스코프(store_scope): 직원 보드(관리자/주방/홀)
     - 테이블 스코프(table_scope): 해당 테이블의 고객 화면
  3. routers/events.py의 SSE 스트림이 큐에서 꺼내 브라우저로 전송한다

여러 uvicorn 워커로 띄워도 DB를 통해 모든 워커가 같은 알림을 받는다.
이벤트는 '다시 가져오라'는 신호일 뿐이라, 큐가 가득 차면 버려도 안전하다.
"""
import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress

import asyncpg
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app2.core.config import settings

logger = logging.getLogger(__name__)

CHANNEL = "order_events"
KEEPALIVE_SECONDS = 15   # SSE 유휴 연결 유지용 주석 프레임 간격
HEALTHCHECK_SECONDS = 30  # LISTEN 연결 생존 확인 간격
QUEUE_SIZE = 32


# =====================================================================
# 발행 (서비스에서 사용)
# =====================================================================
async def notify_order_changed(
    db: AsyncSession, store_id: int, table_id: int, order_id: int, status
) -> None:
    """커밋 직전에 호출. 같은 트랜잭션에서 NOTIFY 하므로 커밋 후에만 전달된다."""
    payload = json.dumps(
        {
            "store_id": store_id,
            "table_id": table_id,
            "order_id": order_id,
            "status": getattr(status, "value", status),
        }
    )
    await db.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": CHANNEL, "payload": payload},
    )


# =====================================================================
# 구독 (SSE 라우터에서 사용)
# =====================================================================
# 구독 범위: ("store", store_id) 또는 ("table", table_id)
Scope = tuple[str, int]


def store_scope(store_id: int) -> Scope:
    return ("store", store_id)


def table_scope(table_id: int) -> Scope:
    return ("table", table_id)


def _frame(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


def _listen_dsn() -> str:
    """SQLAlchemy URL -> asyncpg 직접 연결용 DSN"""
    url = make_url(settings.DATABASE_URL).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


class OrderEventBroker:
    def __init__(self) -> None:
        self._subs: dict[Scope, set[asyncio.Queue]] = {}
        self._task: asyncio.Task | None = None

    # ---------- lifecycle ----------
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._listen_forever(), name="order-event-listener")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        # 열려 있는 SSE 스트림을 종료시켜 서버 종료가 지연되지 않게 한다
        for queues in list(self._subs.values()):
            for q in list(queues):
                self._put(q, None, force=True)

    # ---------- fan-out ----------
    @staticmethod
    def _put(q: asyncio.Queue, item, *, force: bool = False) -> None:
        if force:
            while not q.empty():
                q.get_nowait()
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            pass  # 이미 대기 중인 신호가 있으므로 버려도 된다

    def _dispatch(self, payload: str) -> None:
        """NOTIFY payload -> 해당 매장 구독자 + 해당 테이블 구독자"""
        try:
            data = json.loads(payload)
            scopes = [store_scope(int(data["store_id"]))]
            if data.get("table_id") is not None:
                scopes.append(table_scope(int(data["table_id"])))
        except (ValueError, KeyError, TypeError, AttributeError):
            logger.warning("잘못된 order_events payload: %r", payload)
            return
        for scope in scopes:
            for q in list(self._subs.get(scope, ())):
                self._put(q, ("order_changed", payload))

    def _broadcast_sync(self) -> None:
        for queues in list(self._subs.values()):
            for q in list(queues):
                self._put(q, ("sync", "{}"))

    async def subscribe(self, scope: Scope) -> AsyncIterator[str]:
        """범위(매장/테이블) 단위 SSE 프레임 스트림.
        연결 직후 sync 한 번: 재연결 중 놓친 변경을 클라이언트가 다시 가져오게 한다."""
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subs.setdefault(scope, set()).add(q)
        try:
            yield "retry: 3000\n\n"
            yield _frame("sync", "{}")
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is None:  # 서버 종료
                    return
                yield _frame(*item)
        finally:
            subs = self._subs.get(scope)
            if subs is not None:
                subs.discard(q)
                if not subs:
                    self._subs.pop(scope, None)

    # ---------- LISTEN 연결 ----------
    def _on_notify(self, connection, pid, channel, payload) -> None:
        self._dispatch(payload)

    async def _listen_forever(self) -> None:
        backoff = 1
        while True:
            conn = None
            try:
                conn = await asyncpg.connect(_listen_dsn())
                closed = asyncio.Event()
                conn.add_termination_listener(lambda _c: closed.set())
                await conn.add_listener(CHANNEL, self._on_notify)
                logger.info("order_events LISTEN 시작")
                self._broadcast_sync()  # 끊겨 있던 동안의 변경 보정
                backoff = 1
                while not closed.is_set():
                    try:
                        await asyncio.wait_for(closed.wait(), timeout=HEALTHCHECK_SECONDS)
                    except asyncio.TimeoutError:
                        await conn.execute("SELECT 1")  # 조용히 끊긴 연결 감지
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("order_events LISTEN 연결 오류, %ss 후 재시도", backoff)
            finally:
                if conn is not None and not conn.is_closed():
                    with suppress(Exception):
                        await conn.close()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 15)


broker = OrderEventBroker()
