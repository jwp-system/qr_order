# app2/main.py
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app2.core.config import settings
from app2.core.events import broker
from app2.core.security import require_super_admin
from app2.database.session import engine
from app2.routers import auth, client, events, store, super_admin, super_auth


@asynccontextmanager
async def lifespan(app: FastAPI):
    broker.start()  # 주문 변경 LISTEN 시작 (SSE 푸시)
    yield
    await broker.stop()  # 열린 SSE 스트림 종료
    await engine.dispose()  # DB 커넥션 풀 정리


def _register_middlewares(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            settings.BASE_URL,
            f"http://127.0.0.1:{settings.SERVER_PORT}",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # request.session 용 (스태프 로그인은 core/security.py의 JWT 쿠키를 별도로 사용).
    # request.session을 쓰는 곳이 없다면 제거해도 된다.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        max_age=settings.SESSION_MAX_AGE_SECONDS,
        same_site=settings.SESSION_COOKIE_SAMESITE,
        https_only=settings.SESSION_COOKIE_SECURE,
    )


def _register_routes(app: FastAPI) -> None:
    @app.get("/healthz", tags=["System"])
    async def health_check():
        return {"status": "ok", "app": settings.PROJECT_NAME}

    # tags는 각 라우터에 정의되어 있으므로 include 시 다시 넣지 않는다 (OpenAPI 중복 방지)
    app.include_router(auth.router)         # 스태프 로그인/로그아웃
    app.include_router(client.router)       # 고객 주문
    app.include_router(store.router)        # 식당용: /admin, /kitchen, /hall
    app.include_router(events.router)       # 실시간 이벤트(SSE)
    app.include_router(super_auth.router)   # /super/login, /super/logout (인증 불필요)
    # /super/* 는 전부 시스템 관리자 인증 필수. 라우터 파일이 아니라 여기서 일괄 적용하므로
    # super_admin.py에 라우트가 추가돼도 보호가 빠지지 않는다. 비밀번호 미설정 시 전부 503.
    app.include_router(super_admin.router, dependencies=[Depends(require_super_admin)])


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        debug=settings.DEBUG,
        lifespan=lifespan,
    )
    _register_middlewares(app)
    app.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")
    _register_routes(app)
    return app


app = create_app()