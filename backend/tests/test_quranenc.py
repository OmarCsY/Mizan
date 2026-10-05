from __future__ import annotations

import httpx
import pytest
import respx

from app.core.config import FIXTURES_DIR
from app.sources import SourceUnavailable
from app.sources.quranenc import QuranEncClient, aya_url

SURA_FIXTURE = (FIXTURES_DIR / "quranenc" / "sura_english_rwwad_1.json").read_text(encoding="utf-8")
LIST_FIXTURE = (FIXTURES_DIR / "quranenc" / "translations_list_ur.json").read_text(encoding="utf-8")


@respx.mock
async def test_sura_parses_fixture() -> None:
    respx.get("https://quranenc.com/api/v1/translation/sura/english_rwwad/1").mock(
        return_value=httpx.Response(200, text=SURA_FIXTURE, headers={"content-type": "application/json"})
    )
    rows = await QuranEncClient().sura("english_rwwad", 1)
    assert [(r.surah, r.ayah) for r in rows] == [(1, i) for i in range(1, len(rows) + 1)]
    assert all(r.translation and r.arabic_text for r in rows)


@respx.mock
async def test_list_translations_parses_fixture() -> None:
    respx.get("https://quranenc.com/api/v1/translations/list/ur").mock(
        return_value=httpx.Response(200, text=LIST_FIXTURE, headers={"content-type": "application/json"})
    )
    keys = [t["key"] for t in await QuranEncClient().list_translations("ur")]
    assert "urdu_junagarhi" in keys


@respx.mock
async def test_timeout_raises_source_unavailable_after_one_retry() -> None:
    route = respx.get("https://quranenc.com/api/v1/translation/sura/english_rwwad/1").mock(
        side_effect=httpx.ReadTimeout("t")
    )
    with pytest.raises(SourceUnavailable):
        await QuranEncClient().sura("english_rwwad", 1)
    assert route.call_count == 2


@respx.mock
async def test_unexpected_shape_raises() -> None:
    respx.get("https://quranenc.com/api/v1/translation/sura/english_rwwad/1").mock(
        return_value=httpx.Response(200, json={"oops": []})
    )
    with pytest.raises(SourceUnavailable):
        await QuranEncClient().sura("english_rwwad", 1)


def test_aya_url() -> None:
    assert aya_url("english_rwwad", 2, 255) == "https://quranenc.com/en/browse/english_rwwad/2/255"
