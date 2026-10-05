"""Application settings, read from environment variables (SPEC §14).

Secrets live only in the environment / `.env` (gitignored). Never hard-code values here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
DATA_DIR = REPO_ROOT / "data"
FIXTURES_DIR = BACKEND_ROOT / "tests" / "fixtures"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database (Supabase session pooler or direct URL; see DECISIONS D-7)
    database_url: str = ""

    # LLM
    llm_provider: Literal["anthropic", "openai_compatible", ""] = ""
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model_extract: str = ""
    llm_model_verify: str = ""
    llm_model_reply: str = ""
    llm_timeout_s: float = 8.0
    llm_max_tokens: int = 4096
    llm_effort: str = "low"  # anthropic output_config.effort; empty = omit (e.g. Haiku 4.5)
    llm_anthropic_fallbacks: bool = True  # server-side refusal fallback on models that support it

    # Embeddings
    embedding_provider: Literal["openai_compatible", "cohere", "voyage", ""] = ""
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_model: str = ""
    embedding_dim: int = 1024
    embedding_batch_size: int = 100

    # Sources
    quranenc_base_url: str = "https://quranenc.com"
    quranenc_key_en: str = "english_rwwad"  # pinned in B02, docs/SOURCES.md
    quranenc_key_ur: str = "urdu_junagarhi"
    hadeethenc_base_url: str = "https://hadeethenc.com"
    dorar_base_url: str = "https://dorar.net"
    external_timeout_s: float = 8.0
    external_max_concurrency: int = 4

    # Telegram
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""

    # Web
    public_web_url: str = ""
    allowed_origins: str = "http://localhost:5173"  # comma-separated

    # Limits
    max_input_chars: int = 4000
    result_ttl_hours: int = 24

    log_level: str = "INFO"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
