"""Runtime configuration, read from the environment (FEEDELIO_* variables)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Everything the app needs to know about its environment."""

    model_config = SettingsConfigDict(env_prefix="FEEDELIO_", env_file=".env")

    #: Where the reader SQLite database lives. Mount this path on a volume.
    db_path: Path = Path("data/feedelio.sqlite")

    #: Directory holding the built SPA. Served at "/" when it exists.
    static_dir: Path = Path("static")

    #: Seconds between polling passes in the worker.
    poll_interval: float = 60.0

    #: Local directory feeds may be loaded from (tests and fixtures only).
    feed_root: str | None = None


def get_settings() -> Settings:
    """Build settings from the current environment."""
    return Settings()
