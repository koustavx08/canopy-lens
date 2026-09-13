"""Application configuration.

All settings are environment-driven (12-factor). See `.env.example` at the
repository root for documentation of every variable.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CANOPYLENS_",
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Service identity -------------------------------------------------
    app_name: str = "CanopyLens API"
    environment: str = "development"
    log_level: str = "INFO"

    # --- HTTP -------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Storage ----------------------------------------------------------
    data_dir: Path = REPO_ROOT / "backend" / ".data"
    max_upload_mb: int = 512

    # --- Processing limits ------------------------------------------------
    tile_size: int = 800
    tile_overlap: int = 160
    max_pixels_without_tiling: int = 4_000_000

    # --- Detection defaults ----------------------------------------------
    default_confidence: float = 0.30
    default_min_crown_area_m2: float = 1.0
    default_engine: str = "auto"

    @field_validator("data_dir", mode="after")
    @classmethod
    def _ensure_absolute(cls, value: Path) -> Path:
        return value if value.is_absolute() else (REPO_ROOT / value).resolve()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def results_dir(self) -> Path:
        return self.data_dir / "results"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.upload_dir, self.results_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
