"""HadeethEnc client (SPEC §4; field names confirmed by smoke test B02, see docs/SOURCES.md).

`hadeeths/search/` returns HTTP 400 for every parameter tried (DECISIONS D-8); path C of the hadith
retriever uses a local rapidfuzz index instead.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.sources import USER_AGENT, SourceUnavailable, get_json_with_retry

SOURCE = "hadeethenc"


@dataclass(frozen=True, slots=True)
class Hadeeth:
    id: int
    lang: str
    title: str
    text: str  # `hadeeth` in the requested language
    attribution: str
    grade: str
    explanation: str
    translations: tuple[str, ...]


def hadith_url(hadith_id: int, lang: str = "ar") -> str:
    return f"https://hadeethenc.com/{lang}/browse/hadith/{hadith_id}"


def _as_list(v: object) -> tuple[str, ...]:
    # `translations` / `categories` arrive either as a JSON list or as a Python-repr string "['ar', 'en']"
    if isinstance(v, list):
        return tuple(str(x) for x in v)
    if isinstance(v, str):
        return tuple(x.strip(" '\"") for x in v.strip("[]").split(",") if x.strip(" '\""))
    return ()


class HadeethEncClient:
    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        s = get_settings()
        self.base = s.hadeethenc_base_url.rstrip("/") + "/api/v1"
        self.timeout_s = s.external_timeout_s
        self._client = http_client or httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def categories(self, language: str = "ar") -> list[dict]:
        body = await get_json_with_retry(
            self._client, SOURCE, f"{self.base}/categories/list/", params={"language": language},
            timeout_s=self.timeout_s,
        )
        if not isinstance(body, list):
            raise SourceUnavailable(SOURCE, "unexpected_shape")
        return body

    async def list_page(self, category_id: int, page: int, per_page: int = 500, language: str = "ar") -> tuple[list[dict], int]:
        """Return (items, last_page) for one page of a category listing."""
        body = await get_json_with_retry(
            self._client, SOURCE, f"{self.base}/hadeeths/list/",
            params={"language": language, "category_id": category_id, "page": page, "per_page": per_page},
            timeout_s=max(self.timeout_s, 30.0),  # large pages are slow on a cold cache (~5 s seen in B02)
        )
        try:
            return list(body["data"]), int(body["meta"]["last_page"])  # type: ignore[index]
        except (KeyError, TypeError, ValueError) as e:
            raise SourceUnavailable(SOURCE, "unexpected_shape") from e

    async def one(self, hadith_id: int, language: str) -> Hadeeth:
        body = await get_json_with_retry(
            self._client, SOURCE, f"{self.base}/hadeeths/one/", params={"id": hadith_id, "language": language},
            timeout_s=self.timeout_s,
        )
        return parse_one(body, language)


def parse_one(body: object, language: str) -> Hadeeth:
    try:
        b = dict(body)  # type: ignore[arg-type]
        return Hadeeth(
            id=int(b["id"]),
            lang=language,
            title=b.get("title") or "",
            text=(b.get("hadeeth") or "").strip(),
            attribution=(b.get("attribution") or "").strip(),
            grade=(b.get("grade") or "").strip(),
            explanation=(b.get("explanation") or "").strip(),
            translations=_as_list(b.get("translations")),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise SourceUnavailable(SOURCE, "unexpected_shape") from e
