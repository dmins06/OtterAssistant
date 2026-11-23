from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = Field(default="Otter Assistant")
    app_env: str = Field(default="development")
    app_debug: bool = Field(default=False)
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8000)
    app_timezone: str = Field(default="Asia/Ho_Chi_Minh")
    scheduler_enabled: bool = Field(default=False)
    cors_origins: str = Field(default="http://localhost:5173,http://127.0.0.1:5173")

    # Security
    secret_key: str = Field(default="dev-only-change-in-production")
    encryption_key: str = Field(default="")
    jwt_algorithm: str = Field(default="HS256")
    jwt_access_token_expire_minutes: int = Field(default=60)

    # Database (MySQL 8.0+ / TiDB Cloud — Source of Truth)
    database_url: str = Field(
        default="mysql+pymysql://otter_user:otter_dev_pw@localhost:3306/otter_assistant?charset=utf8mb4"
    )
    database_ssl_required: bool = Field(default=False)
    database_ssl_ca: str = Field(default="")

    # Redis (Cache, Rate Limiting, Distributed Locks ONLY)
    redis_url: str = Field(default="redis://localhost:6379/0")

    # Google Gemini API (Free Tier)
    gemini_api_key: str = Field(default="")
    gemini_model: str = Field(default="gemini-3.8-flash")
    gemini_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    gemini_max_output_tokens: int = Field(default=1024, ge=1)

    # UTH Integrations
    portal_base_url: str = Field(default="https://portal.ut.edu.vn")
    portal_schedule_endpoint: str = Field(default="/api/v1/lichhoc/lichTuan")
    portal_refresh_endpoint: str = Field(default="")
    portal_token_refresh_buffer_seconds: int = Field(default=300, ge=30)
    portal_use_mock: bool = Field(default=False)
    portal_semester_start_date: str = Field(default="")
    portal_semester_end_date: str = Field(default="")
    portal_semester_max_weeks: int = Field(default=24, ge=1, le=52)
    portal_week_request_delay_seconds: float = Field(default=0.3, ge=0.0, le=10.0)
    portal_auto_sync_interval_hours: int = Field(default=6, ge=1, le=168)
    lms_base_url: str = Field(default="https://courses.ut.edu.vn")
    lms_use_mock: bool = Field(default=False)

    # Telegram Bot API
    telegram_bot_token: str = Field(default="")
    telegram_bot_username: str = Field(default="")
    telegram_webhook_secret: str = Field(default="")

    @field_validator("app_env")
    @classmethod
    def validate_app_env(cls, v: str) -> str:
        allowed = {"development", "testing", "staging", "production"}
        normalized = v.strip().lower()
        if normalized not in allowed:
            raise ValueError(f"APP_ENV must be one of {sorted(allowed)}")
        return normalized

    @property
    def cors_origin_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
