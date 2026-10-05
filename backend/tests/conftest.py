from __future__ import annotations

import os

import pytest

# Unit tests must never reach real services through values in .env (DB, model keys). Live tests (--live)
# keep the environment as is; set DATABASE_URL in the shell to point them at a local or test database.
_ISOLATED_VARS = ("DATABASE_URL", "LLM_API_KEY", "EMBEDDING_API_KEY", "GROQ_API_KEY", "TELEGRAM_BOT_TOKEN")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--live", action="store_true", default=False, help="run tests marked @pytest.mark.live")


def pytest_configure(config: pytest.Config) -> None:
    if not config.getoption("--live"):
        for var in _ISOLATED_VARS:
            os.environ[var] = ""  # env vars take precedence over .env in pydantic-settings
        from app.core.config import get_settings

        get_settings.cache_clear()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live"):
        return
    skip_live = pytest.mark.skip(reason="live test; pass --live to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
