"""In-memory Mushaf index (B05). Needs data/quran.json: run `python scripts/ingest_quran.py` first."""

from __future__ import annotations

import time

import pytest

from app.pipeline import quran_match

pytestmark = pytest.mark.skipif(
    not quran_match.QURAN_JSON.exists(), reason="data/quran.json missing: run python scripts/ingest_quran.py"
)


@pytest.fixture(scope="module")
def index() -> quran_match.QuranIndex:
    return quran_match.load_from_json()


def test_exact_verse_count(index: quran_match.QuranIndex) -> None:
    assert len(index.verses) == 6236
    assert index.n_surahs == 114
    assert [v.id for v in index.verses] == list(range(1, 6237))


def test_lookup_by_reference(index: quran_match.QuranIndex) -> None:
    for ref in ((1, 1), (2, 255), (114, 6)):
        v = index.verse(*ref)
        assert v is not None and (v.surah, v.ayah) == ref
        assert v.uthmani and v.clean and v.imlaei_clean and v.surah_name_ar and v.surah_name_en
        assert "۝" not in v.uthmani  # end-of-ayah sign stripped
    assert index.verse(1, 8) is None


def test_windows_stay_inside_one_surah(index: quran_match.QuranIndex) -> None:
    sizes = {1: 0, 2: 0, 3: 0}
    for w in index.windows:
        span = index.verses[w.start : w.start + w.size]
        assert len({v.surah for v in span}) == 1
        sizes[w.size] += 1
    assert sizes == {1: 6236, 2: 6236 - 114, 3: 6236 - 2 * 114}
    assert len(index.corpus_clean) == len(index.corpus_imlaei) == len(index.windows)


def test_both_matching_forms_present_and_differ_somewhere(index: quran_match.QuranIndex) -> None:
    # D-3: imla'i must add information, i.e. differ from normalized Uthmani for some verses
    assert any(v.clean != v.imlaei_clean for v in index.verses)
    assert all(v.imlaei_clean for v in index.verses)


def test_load_is_fast() -> None:
    t0 = time.perf_counter()
    quran_match.load_from_json()
    assert time.perf_counter() - t0 < 3.0
