"""Live DB tests: need DATABASE_URL pointing at a migrated database. Run with --live."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.db import queries, session

pytestmark = pytest.mark.live


@pytest.fixture
async def pool():
    if not get_settings().database_url:
        pytest.skip("DATABASE_URL not set")
    session._pool = None
    p = await session.get_pool()
    yield p
    await session.close_pool()


async def test_expired_results_are_deleted(pool) -> None:
    now = datetime.now(timezone.utc)
    old, fresh = uuid.uuid4().hex, uuid.uuid4().hex
    await queries.save_check_result(old, {"status": "ok"}, now - timedelta(minutes=1))
    await queries.save_check_result(fresh, {"status": "ok"}, now + timedelta(hours=24))
    assert await queries.delete_expired_results() >= 1
    assert await queries.get_check_result(old) is None
    got = await queries.get_check_result(fresh)
    assert got is not None and got["result"] == {"status": "ok"}
    await pool.execute("delete from check_results where check_id = $1", fresh)


async def test_dorar_cache_roundtrip(pool) -> None:
    h = uuid.uuid4().hex
    await queries.dorar_cache_put(h, "q", [{"id": "dorar:abc", "grade_text": "x"}])
    assert await queries.dorar_cache_get(h) == [{"id": "dorar:abc", "grade_text": "x"}]
    await pool.execute("delete from dorar_cache where query_hash = $1", h)
