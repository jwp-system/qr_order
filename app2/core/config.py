from pathlib import Path
from typing import Literal

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parents[1]  # .../app
_INSECURE_SECRET_KEY = "quickorder-insecure-secret-key-change-in-production-123456789"


class Settings(BaseSettings):
    # --- 애플리케이션 기본 정보 ---
    PROJECT_NAME: str = "QuickOrder SaaS"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    DEBUG: bool = False
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"

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
    DATABASE_URL_OVERRIDE: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_URL(self) -> str:
        if self.DATABASE_URL_OVERRIDE:
            return self.DATABASE_URL_OVERRIDE
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # DB Connection Pool 설정 (database/session.py에서 사용)
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_PRE_PING: bool = True

    # --- 보안 및 인증 (Security & Tokens) ---
    # 테이블 QR 토큰 서명 및 스태프 세션 쿠키 서명용 비밀키
    SECRET_KEY: str = _INSECURE_SECRET_KEY
    ALGORITHM: str = "HS256"

    # 세션 쿠키 설정
    SESSION_COOKIE_NAME: str = "quickorder_session"
    SESSION_MAX_AGE_SECONDS: int = 60 * 60 * 24  # 24시간
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SESSION_COOKIE_SECURE(self) -> bool:
        """development 외(HTTPS 전제)에서는 Secure 쿠키"""
        return self.ENVIRONMENT != "development"

    # --- 파일 경로 (실행 위치와 무관하게 app/ 기준 절대경로) ---
    STATIC_DIR: str = str(APP_DIR / "static")
    TEMPLATES_DIR: str = str(APP_DIR / "templates")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @model_validator(mode="after")
    def _check_production_secrets(self) -> "Settings":
        if self.ENVIRONMENT == "production" and self.SECRET_KEY == _INSECURE_SECRET_KEY:
            raise ValueError("production 환경에서는 SECRET_KEY를 반드시 변경해야 합니다.")
        return self


# 전역 인스턴스 싱글톤 생성
settings = Settings()