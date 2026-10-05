"""QuranEnc client (SPEC §4, field names confirmed by smoke test B02, see docs/SOURCES.md)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.sources import USER_AGENT, SourceUnavailable, get_json_with_retry

SOURCE = "quranenc"


@dataclass(frozen=True, slots=True)
class TranslatedAya:
    surah: int
    ayah: int
    arabic_text: str
    translation: str
    footnotes: str


def aya_url(key: str, surah: int, ayah: int) -> str:
    """Public page for a verse in a given translation (shown as the evidence link)."""
    return f"https://quranenc.com/en/browse/{key}/{surah}/{ayah}"


class QuranEncClient:
    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        s = get_settings()
        self.base = s.quranenc_base_url.rstrip("/") + "/api/v1"
        self.timeout_s = s.external_timeout_s
        self._client = http_client or httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_translations(self, lang: str) -> list[dict]:
        body = await get_json_with_retry(
            self._client, SOURCE, f"{self.base}/translations/list/{lang}", timeout_s=self.timeout_s
        )
        try:
            return list(body["translations"])  # type: ignore[index]
        except (KeyError, TypeError) as e:
            raise SourceUnavailable(SOURCE, "unexpected_shape") from e

    async def sura(self, key: str, surah: int) -> list[TranslatedAya]:
        body = await get_json_with_retry(
            self._client, SOURCE, f"{self.base}/translation/sura/{key}/{surah}", timeout_s=self.timeout_s
        )
        try:
            return [
                TranslatedAya(
                    surah=int(r["sura"]),
                    ayah=int(r["aya"]),
                    arabic_text=r.get("arabic_text") or "",
                    translation=r["translation"],
                    footnotes=r.get("footnotes") or "",
                )
                for r in body["result"]  # type: ignore[index]
            ]
        except (KeyError, TypeError, ValueError) as e:
            raise SourceUnavailable(SOURCE, "unexpected_shape") from e
