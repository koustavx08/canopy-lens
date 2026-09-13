"""Configuration behaviour that the rest of the app relies on."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings


def test_cors_origins_are_split_and_trimmed() -> None:
    settings = Settings(cors_origins="http://a.test , http://b.test,")
    assert settings.cors_origin_list == ["http://a.test", "http://b.test"]


def test_relative_data_dir_is_resolved_against_repo_root() -> None:
    settings = Settings(data_dir=Path("backend/.data"))
    assert settings.data_dir.is_absolute()


def test_ensure_dirs_creates_upload_and_result_folders(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "store")
    settings.ensure_dirs()
    assert settings.upload_dir.is_dir()
    assert settings.results_dir.is_dir()


def test_environment_prefix_is_honoured(monkeypatch) -> None:
    monkeypatch.setenv("CANOPYLENS_TILE_SIZE", "512")
    assert Settings().tile_size == 512
