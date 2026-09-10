from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPOSITORY_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "EvoSRE"
    app_env: str = "development"
    database_path: Path = REPOSITORY_ROOT / "backend" / "data" / "evosre.db"
    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    openai_model: str = "gpt-5.6-luna"
    investigation_step_delay_ms: int = 35
    otel_lab_url: str = "http://127.0.0.1:8010"
    prometheus_url: str = "http://127.0.0.1:9090"
    loki_url: str = "http://127.0.0.1:3100"
    tempo_url: str = "http://127.0.0.1:3200"
    otel_lab_token: str = "evosre-local-lab-token"
    observability_timeout_seconds: float = 3.0

    @field_validator("database_path")
    @classmethod
    def resolve_database_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else REPOSITORY_ROOT / value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
