from __future__ import annotations

from app.core.messages import DISCLAIMER, PRIVACY, localized


def test_every_message_has_ar_en_ur() -> None:
    for table in (DISCLAIMER, PRIVACY):
        assert set(table) == {"ar", "en", "ur"} and all(v.strip() for v in table.values())


def test_privacy_mentions_free_tier_provider_use() -> None:  # D-15
    assert "free tier" in PRIVACY["en"] and "improve its services" in PRIVACY["en"]
    assert "الخطة المجانية" in PRIVACY["ar"]
    assert "مفت" in PRIVACY["ur"]


def test_localized_falls_back_to_arabic() -> None:
    assert localized(DISCLAIMER, "fr") == DISCLAIMER["ar"]
