"""Dorar client tests against the real responses saved by scripts/smoke_sources.py (B02)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from bs4 import BeautifulSoup

from app.core.config import FIXTURES_DIR
from app.sources import SourceUnavailable
from app.sources.dorar import DorarClient, cache_key, parse_results, stable_id

DORAR_URL = "https://dorar.net/dorar_api.json"
FIX = FIXTURES_DIR / "dorar"


def load(name: str) -> tuple[str, str, str]:
    """(raw body text, html, query) for a saved fixture."""
    raw = (FIX / f"{name}.json").read_text(encoding="utf-8")
    meta = json.loads((FIX / f"{name}.meta.json").read_text(encoding="utf-8"))
    return raw, json.loads(raw)["ahadith"]["result"], meta["params"]["skey"]


def test_parses_fixture_count_and_fields() -> None:
    _, html, query = load("api_plain")
    soup = BeautifulSoup(html, "lxml")
    n_html = len(soup.select("div.hadith"))
    results = parse_results(html, query)
    assert n_html == 15 and len(results) == n_html
    infos = soup.select("div.hadith-info")
    for r, info in zip(results, infos, strict=True):
        labels = {s.get_text(strip=True) for s in info.select("span.info-subtitle")}
        assert r.text and r.text_clean
        # every field is non-empty where the HTML has its label
        for label, value in (
            ("الراوي:", r.narrator), ("المحدث:", r.mohaddith), ("المصدر:", r.book),
            ("الصفحة أو الرقم:", r.page), ("خلاصة حكم المحدث:", r.grade_text),
        ):
            if label in labels:
                assert value, f"{label} empty for {r.id}"
        assert r.url.startswith("https://dorar.net/hadith/search?q=")
        assert not r.text[0].isdigit()  # result numbering stripped


def test_ids_are_stable_and_unique() -> None:
    _, html, query = load("api_plain")
    a, b = parse_results(html, query), parse_results(html, query)
    assert [r.id for r in a] == [r.id for r in b]
    assert len({r.id for r in a}) == len(a)
    r = a[0]
    assert r.id == stable_id(r.text_clean, r.mohaddith, r.book, r.page)
    assert r.id.startswith("dorar:") and len(r.id) == len("dorar:") + 12


def test_sahihayn_fixture_books() -> None:
    _, html, query = load("api_sahihayn")
    assert {r.book for r in parse_results(html, query)} <= {"صحيح البخاري", "صحيح مسلم"}


def test_no_results_is_empty_list() -> None:
    _, html, query = load("api_empty")
    assert parse_results(html, query) == []


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, list[dict]] = {}

    async def get(self, key: str) -> list[dict] | None:
        return self.data.get(key)

    async def put(self, key: str, query: str, response: list[dict]) -> None:
        self.data[key] = response


@respx.mock
async def test_second_identical_query_served_from_cache() -> None:
    raw, _, query = load("api_plain")
    route = respx.get(DORAR_URL).mock(
        return_value=httpx.Response(200, text=raw, headers={"content-type": "application/json"})
    )
    cache = MemoryCache()
    client = DorarClient(cache_get=cache.get, cache_put=cache.put)
    first = await client.search(query)
    second = await client.search(query)
    assert route.call_count == 1
    assert first == second and len(first) == 15


@respx.mock
async def test_filters_change_cache_key_and_params() -> None:
    raw, _, query = load("api_sahihayn")
    route = respx.get(DORAR_URL).mock(
        return_value=httpx.Response(200, text=raw, headers={"content-type": "application/json"})
    )
    cache = MemoryCache()
    client = DorarClient(cache_get=cache.get, cache_put=cache.put)
    await client.search(query, books=("6216", "3088"))
    assert cache_key(query) != cache_key(query, ("6216", "3088"))
    assert route.calls[0].request.url.params.get_list("s[]") == ["6216", "3088"]


@respx.mock
async def test_timeout_raises_source_unavailable() -> None:
    route = respx.get(DORAR_URL).mock(side_effect=httpx.ReadTimeout("t"))
    with pytest.raises(SourceUnavailable):
        await DorarClient(cache_get=None, cache_put=None).search("x")
    assert route.call_count == 2  # one retry, never an empty list


@respx.mock
async def test_cloudflare_block_raises_source_unavailable() -> None:
    respx.get(DORAR_URL).mock(return_value=httpx.Response(403, text="<html>Attention Required!</html>"))
    with pytest.raises(SourceUnavailable):
        await DorarClient(cache_get=None, cache_put=None).search("x")


@respx.mock
async def test_html_instead_of_json_raises() -> None:
    respx.get(DORAR_URL).mock(return_value=httpx.Response(200, text="<html>challenge</html>"))
    with pytest.raises(SourceUnavailable):
        await DorarClient(cache_get=None, cache_put=None).search("x")


@respx.mock
async def test_cache_outage_does_not_block_live_call() -> None:
    raw, _, query = load("api_plain")
    respx.get(DORAR_URL).mock(return_value=httpx.Response(200, text=raw, headers={"content-type": "application/json"}))

    async def broken_get(key: str) -> list[dict] | None:
        raise OSError("db down")

    async def broken_put(key: str, q: str, r: list[dict]) -> None:
        raise OSError("db down")

    results = await DorarClient(cache_get=broken_get, cache_put=broken_put).search(query)
    assert len(results) == 15


@pytest.mark.live
async def test_live_search_uses_db_cache() -> None:
    """Real Dorar call + real dorar_cache (needs network and DATABASE_URL)."""
    from app.core.config import get_settings
    from app.db import session

    if not get_settings().database_url:
        pytest.skip("DATABASE_URL not set")
    _, _, query = load("api_plain")
    session._pool = None
    client = DorarClient()
    try:
        first = await client.search(query)
        assert first, "Dorar returned no results for a query taken from a HadeethEnc title"
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(DORAR_URL)
            second = await client.search(query)
            assert route.call_count == 0  # served from dorar_cache
        assert [r.id for r in second] == [r.id for r in first]
    finally:
        await client.aclose()
        await session.close_pool()
