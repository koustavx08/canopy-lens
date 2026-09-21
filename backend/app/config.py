"""Application configuration.

All settings are environment-driven (12-factor). See `.env.example` at the
repository root for documentation of every variable.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
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
    # Boundaries are XML or JSON describing an outline; a few megabytes is
    # already a very detailed one. A separate, much lower ceiling keeps a
    # 512 MB imagery allowance from applying to a text file.
    max_boundary_upload_mb: int = 32

    # --- Access control ---------------------------------------------------
    # Comma-separated keys accepted in `X-API-Key` (or as a bearer token).
    # Empty means the API is open, which is the right default for a laptop and
    # the wrong one for a public address - so the readiness probe says so when
    # the environment is not "development".
    api_keys: str = ""
    # Requests per minute per client, across every endpoint but the probes.
    # 0 disables the limit. Set well above what the interface needs: it polls
    # a running job about once a second, and a limit tight enough to be felt
    # by one honest browser is a limit that will be turned off rather than
    # tuned. This is a ceiling on floods, not a quota on use.
    rate_limit_per_minute: int = 600
    # How many reverse proxies sit in front of this server. 0 means it is
    # exposed directly and `X-Forwarded-For` is a header a client wrote, so it
    # is ignored. 1 is right behind the nginx in docker-compose, or behind
    # Render's router: the address that proxy appended is the real client, and
    # anything the client put in front of it cannot be mistaken for one.
    trusted_proxy_hops: int = 0

    # --- Processing limits ------------------------------------------------
    # How many analyses may run at the same time. Detection is CPU- and
    # memory-bound, so letting every upload start at once does not finish any
    # of them sooner: it makes all of them slow and risks exhausting the box.
    # Work above this waits in the queue the job model already describes.
    max_concurrent_analyses: int = 2
    # How many analyses may be waiting to start. Reached, the server says so
    # with a 429 rather than accepting an upload it has no prospect of getting
    # to: a queue that grows without limit turns a busy minute into a backlog
    # that is still being worked through long after the callers gave up.
    max_queued_analyses: int = 20
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
    def api_key_set(self) -> frozenset[str]:
        return frozenset(k.strip() for k in self.api_keys.split(",") if k.strip())

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
