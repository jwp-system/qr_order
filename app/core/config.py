import os
from typing import List, Union
from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- 애플리케이션 기본 정보 ---
    PROJECT_NAME: str = "QuickOrder SaaS"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"  # development, staging, production

    # --- 서버 호스트 & 도메인 ---
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000

    @computed_field  # type: ignore[prop-decorator]
    @property
    def BASE_URL(self) -> str:
        return f"http://localhost:{self.SERVER_PORT}"

    # --- 데이터베이스 (PostgreSQL + asyncpg) ---
    POSTGRES_USER: str = "quickorder"
    POSTGRES_PASSWORD: str = "quickorder_dev_secret"
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "quickorder"

    # 직접 DATABASE_URL을 주입하거나(Docker/운영 환경), 위 설정값으로 비동기 DSN 조립
    DATABASE_URL_OVERRIDE: Union[str, None] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_URL(self) -> str:
        if self.DATABASE_URL_OVERRIDE:
            return self.DATABASE_URL_OVERRIDE
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # DB Connection Pool 설정
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_PRE_PING: bool = True

    # --- 보안 및 인증 (Security & Tokens) ---
    # 테이블 QR 토큰 검증(HMAC) 및 관리자 세션 쿠키 서명용 비밀키
    SECRET_KEY: str = "quickorder-insecure-secret-key-change-in-production-123456789"
    ALGORITHM: str = "HS256"
    
    # 세션 쿠키 설정
    SESSION_COOKIE_NAME: str = "quickorder_session"
    SESSION_MAX_AGE_SECONDS: int = 60 * 60 * 24  # 24시간
    # SESSION_COOKIE_SECURE: bool = False  # Production 배포 시 HTTPS 적용 후 True 변경
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "lax"


    # --- PWA 및 파일 경로 ---
    STATIC_DIR: str = "app/static"
    TEMPLATES_DIR: str = "app/templates"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )


# 전역 인스턴스 싱글톤 생성
settings = Settings()