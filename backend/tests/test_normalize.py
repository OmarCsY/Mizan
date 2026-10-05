"""Normalization tests (SPEC §7.1). Ordinary Arabic words only; scripture comes from fixtures by reference."""

from __future__ import annotations

import json

from app.core.config import FIXTURES_DIR
from app.pipeline.normalize import normalize_ar, tokens


def test_removes_tashkeel() -> None:
    assert normalize_ar("مَدْرَسَتُنَا") == "مدرستنا"
    assert normalize_ar("كِتَابٌ") == "كتاب"  # tanween


def test_removes_open_tanween_without_splitting_word() -> None:  # D-13
    assert normalize_ar("كتابࣱ جديدࣰ") == "كتاب جديد"


def test_removes_tatweel() -> None:
    assert normalize_ar("كتــــاب") == "كتاب"


def test_hamza_forms_to_bare_alef() -> None:
    assert normalize_ar("إبراهيم أحمد آمن ٱلعلم") == "ابراهيم احمد امن العلم"


def test_ta_marbuta_and_alif_maqsura() -> None:
    assert normalize_ar("مدرسة") == "مدرسه"
    assert normalize_ar("مستشفى") == "مستشفي"


def test_hamza_on_waw_and_ya() -> None:
    assert normalize_ar("مؤمن") == "مومن"
    assert normalize_ar("سائل") == "سايل"


def test_removes_punctuation_digits_and_latin() -> None:
    assert normalize_ar("درس، رقم 12 (٣٤) - lesson!؟") == "درس رقم"


def test_removes_invisible_marks() -> None:
    assert normalize_ar("در‏س‎") == "درس"


def test_collapses_whitespace() -> None:
    assert normalize_ar("  كتاب \n\t قلم  ") == "كتاب قلم"


def test_tokens() -> None:
    assert tokens("كتاب قلم") == ["كتاب", "قلم"]
    assert tokens("") == []


def test_uthmani_and_imlaei_agree_on_fixture_verse() -> None:
    rows = json.loads((FIXTURES_DIR / "mushaf" / "kfgqpc_hafs_v30_sample.json").read_text(encoding="utf-8"))
    first = next(r for r in rows if (int(r["sura_no"]), int(r["aya_no"])) == (1, 1))
    uthmani = first["aya_text_unicode"]
    # the end-of-ayah sign and verse number are removed; letters agree between the two forms
    assert normalize_ar(uthmani) == normalize_ar(first["aya_text_emlaey"])
