"""Application configuration loaded from environment variables."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    google_api_key: Optional[str] = None
    gemini_model: str = "gemini-3.5-flash-lite"
    kb_path: Path = Path("knowledge-base")
    accounts_path: Path = Path("data/accounts.json")
    tickets_path: Path = Path("data/tickets.json")

    def require_api_key(self) -> str:
        """Return the API key or raise a clear error if it is missing."""
        key = self.google_api_key
        if not key or key.strip() == "your-gemini-api-key-here":
            raise RuntimeError(
                "GOOGLE_API_KEY is not set. "
                "Copy .env.example to .env and add your Gemini API key."
            )
        return key


# Module-level singleton — instantiation never raises; errors surface at call time.
settings = Settings()  # type: ignore[call-arg]
