from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.core.config import FIXTURES_DIR
from app.sources import SourceUnavailable
from app.sources.hadeethenc import HadeethEncClient, hadith_url, parse_one

FIX = FIXTURES_DIR / "hadeethenc"


def fixture_one(lang: str) -> tuple[int, dict]:
    path = next(FIX.glob(f"one_*_{lang}.json"))
    return int(path.stem.split("_")[1]), json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("lang", ["ar", "en", "ur"])
def test_parse_one_fixture(lang: str) -> None:
    hid, body = fixture_one(lang)
    h = parse_one(body, lang)
    assert h.id == hid and h.lang == lang
    assert h.text and h.attribution and h.grade
    assert {"ar", "en", "ur"} <= set(h.translations)


@respx.mock
async def test_list_page_parses_fixture() -> None:
    path = next(FIX.glob("list_cat*_p1.json"))
    cat = int(path.stem.split("_")[1].removeprefix("cat"))
    respx.get("https://hadeethenc.com/api/v1/hadeeths/list/").mock(
        return_value=httpx.Response(200, text=path.read_text(encoding="utf-8"), headers={"content-type": "application/json"})
    )
    items, last = await HadeethEncClient().list_page(cat, 1, per_page=5)
    assert len(items) == 5 and last >= 1 and all(it["id"] for it in items)


@respx.mock
async def test_one_timeout_raises_source_unavailable() -> None:
    route = respx.get("https://hadeethenc.com/api/v1/hadeeths/one/").mock(side_effect=httpx.ConnectTimeout("t"))
    with pytest.raises(SourceUnavailable):
        await HadeethEncClient().one(1, "ar")
    assert route.call_count == 2


def test_hadith_url() -> None:
    assert hadith_url(5907, "en") == "https://hadeethenc.com/en/browse/hadith/5907"
