# app/database/session.py
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,  # e.g., "postgresql+asyncpg://user:pass@localhost:5432/quickorder"
    echo=False,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_pre_ping=settings.DB_POOL_PRE_PING,
)

# expire_on_commit=False: 커밋 후에도 로드된 속성을 유지 (async에서 lazy load 방지).
# store_service._transition의 멱등 경로가 이 설정에 의존한다.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    # async with가 종료 시 session.close()를 호출하므로 별도 finally 불필요
    async with AsyncSessionLocal() as session:
        yield session
