# app/main.py
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.events import broker
from app.core.security import require_super_admin
from app.core.templating import templates
from app.database.session import engine
from app.routers import auth, client, events, menu, store, super_admin, super_auth, tables


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


def _register_routes(app: FastAPI) -> None:
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def landing(request: Request):
        """서비스 소개 페이지"""
        return templates.TemplateResponse(request=request, name="landing.html")

    @app.get("/healthz", tags=["System"])
    async def health_check():
        return {"status": "ok", "app": settings.PROJECT_NAME}

    # 브라우저가 HTML이 아닌 응답(JSON 등)에서도 자동으로 요청하는 기본 경로
    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return FileResponse(
            f"{settings.STATIC_DIR}/favicon/favicon.ico",
            media_type="image/x-icon",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    # tags는 각 라우터에 정의되어 있으므로 include 시 다시 넣지 않는다 (OpenAPI 중복 방지)
    app.include_router(auth.router)         # 스태프 로그인/로그아웃
    app.include_router(client.router)       # 고객 주문
    app.include_router(store.router)        # 식당용: /admin, /kitchen, /hall
    app.include_router(menu.router)         # 매장 관리자 메뉴 관리: /admin/menu
    app.include_router(tables.router)       # 매장 관리자 테이블 관리: /admin/tables
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
