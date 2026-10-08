# main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.core.config import settings
from app.database.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 애플리케이션 시작 시 DB 연결 확인 등
    yield
    # 종료 시 DB 커넥션 풀 정리
    await engine.dispose()



app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    debug=settings.DEBUG,
    lifespan=lifespan
)

# CORS 미들웨어 등록
CORS_ORIGINS = [
    settings.BASE_URL,
    f"http://127.0.0.1:{settings.SERVER_PORT}",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 세션 미들웨어 등록
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    max_age=settings.SESSION_MAX_AGE_SECONDS
)

# 정적 파일 & Jinja2 템플릿 마운트
app.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")
templates = Jinja2Templates(directory=settings.TEMPLATES_DIR)


# 헬스체크 및 기본 진입점
@app.get("/healthz", tags=["System"])
async def health_check():
    return {"status": "ok", "app": settings.PROJECT_NAME}


# TODO: 도메인별 라우터 등록 예정
# from app.routers import client, store_admin, kitchen, hall
# app.include_router(client.router, prefix="", tags=["Client Order"])
# app.include_router(store_admin.router, prefix="/admin", tags=["Store Admin"])
# app.include_router(kitchen.router, prefix="/kitchen", tags=["Kitchen KDS"])
# app.include_router(hall.router, prefix="/hall", tags=["Hall Serving"])

# 클라이언트 주문 라우터 마운트
from app.routers import client
app.include_router(client.router, prefix="", tags=["Client Order"])

# 매장 관리자 라우터 마운트
from app.routers import store_admin
app.include_router(store_admin.router, tags=["Store Admin"])

# 슈퍼 관리자 라우터 마운트
from app.routers import super_admin
app.include_router(super_admin.router, tags=["Super Admin"])