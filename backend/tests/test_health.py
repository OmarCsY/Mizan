from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db import session
from app.main import app


@pytest.fixture
def unreachable_db(monkeypatch: pytest.MonkeyPatch):
    # Port 1 on localhost refuses connections immediately.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
    get_settings.cache_clear()
    session._pool = None
    yield
    get_settings.cache_clear()
    session._pool = None


def test_health_503_when_db_unreachable(unreachable_db: None) -> None:
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 503
    assert r.json() == {"ok": False, "db": False}


def test_health_200_when_db_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_ping(timeout_s: float = 3.0) -> bool:
        return True

    monkeypatch.setattr(session, "ping", fake_ping)
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "db": True}


@pytest.mark.live
def test_health_200_against_real_db() -> None:
    """Needs DATABASE_URL pointing at a migrated database."""
    if not os.environ.get("DATABASE_URL") and not get_settings().database_url:
        pytest.skip("DATABASE_URL not set")
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
