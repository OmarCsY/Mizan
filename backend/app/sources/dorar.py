"""Dorar hadith search client (SPEC §4.1, AMENDMENT 11; shapes confirmed by smoke test B02).

The official endpoint `dorar_api.json?skey=<query>` returns JSON whose `ahadith.result` is an HTML string
with up to 15 results: `div.hadith` (text) followed by `div.hadith-info` (labelled fields). Results have
no per-hadith id or URL (DECISIONS D-9), so each gets a stable id
`dorar:<sha1(normalized text + scholar + book + page)[:12]>` and the response's "المزيد" search link.

Parsed results are cached in `dorar_cache` keyed by sha1 of the normalized query plus filters. A failed
call raises SourceUnavailable; an empty list means Dorar answered with no results.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup, Tag

from app.core.config import get_settings
from app.core.logging import get_logger
from app.pipeline.normalize import normalize_ar
from app.sources import USER_AGENT, SourceUnavailable, get_json_with_retry

log = get_logger(__name__)

SOURCE = "dorar"

# Book ids for the `s[]` filter, confirmed in B02 (docs/SOURCES.md). Use only if D-11 is approved.
BOOK_BUKHARI = "6216"
BOOK_MUSLIM = "3088"

LABELS = {
    "الراوي": "narrator",
    "المحدث": "mohaddith",
    "المصدر": "book",
    "الصفحة أو الرقم": "page",
    "خلاصة حكم المحدث": "grade_text",
}
_LEADING_NUMBER = re.compile(r"^\s*\d+\s*-\s*")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class DorarResult:
    id: str
    text: str
    text_clean: str
    narrator: str
    mohaddith: str
    book: str
    page: str
    grade_text: str
    url: str


def search_url(query: str) -> str:
    return f"https://dorar.net/hadith/search?q={quote(query)}"


def stable_id(text_clean: str, mohaddith: str, book: str, page: str) -> str:
    key = "|".join((text_clean, normalize_ar(mohaddith), normalize_ar(book), page.strip()))
    return "dorar:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _clean(s: str) -> str:
    return _SPACES.sub(" ", s).strip()


def _parse_info(info: Tag) -> dict[str, str]:
    """Collect the text after each `span.info-subtitle` label until the next label."""
    fields: dict[str, str] = {}
    labels = info.select("span.info-subtitle")
    for label in labels:
        name = LABELS.get(label.get_text(strip=True).rstrip(":").strip())
        if not name:
            continue
        parts: list[str] = []
        for sib in label.next_siblings:
            if isinstance(sib, Tag) and "info-subtitle" in (sib.get("class") or []):
                break
            parts.append(sib.get_text(" ") if isinstance(sib, Tag) else str(sib))
        fields[name] = _clean("".join(parts))
    return fields


def parse_results(html: str, query: str) -> list[DorarResult]:
    soup = BeautifulSoup(html, "lxml")
    more = soup.find("a", string=lambda s: bool(s) and "المزيد" in s)
    url = more["href"] if more and more.get("href") else search_url(query)
    results: list[DorarResult] = []
    for div in soup.select("div.hadith"):
        info = div.find_next_sibling("div", class_="hadith-info")
        text = _clean(_LEADING_NUMBER.sub("", div.get_text(" ")))
        text = re.sub(r"\s+([.،,:])", r"\1", text).rstrip(" .")
        f = _parse_info(info) if info else {}
        text_clean = normalize_ar(text)
        results.append(
            DorarResult(
                id=stable_id(text_clean, f.get("mohaddith", ""), f.get("book", ""), f.get("page", "")),
                text=text,
                text_clean=text_clean,
                narrator=f.get("narrator", ""),
                mohaddith=f.get("mohaddith", ""),
                book=f.get("book", ""),
                page=f.get("page", ""),
                grade_text=f.get("grade_text", ""),
                url=str(url),
            )
        )
    return results


def cache_key(query: str, books: tuple[str, ...] = (), page: int | None = None) -> str:
    sig = normalize_ar(query) + "|s=" + ",".join(sorted(books)) + "|p=" + str(page or 1)
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()


CacheGet = Callable[[str], Awaitable[list[dict] | None]]
CachePut = Callable[[str, str, list[dict]], Awaitable[None]]


async def _db_cache_get(key: str) -> list[dict] | None:
    from app.db import queries

    return await queries.dorar_cache_get(key)


async def _db_cache_put(key: str, query: str, response: list[dict]) -> None:
    from app.db import queries

    await queries.dorar_cache_put(key, query, response)


class DorarClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        *,
        cache_get: CacheGet | None = _db_cache_get,
        cache_put: CachePut | None = _db_cache_put,
    ) -> None:
        s = get_settings()
        self.url = s.dorar_base_url.rstrip("/") + "/dorar_api.json"
        self.timeout_s = s.external_timeout_s
        # A descriptive User-Agent is required: Dorar's Cloudflare returns 403 to the default library UA (D-9).
        self._client = http_client or httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        self._cache_get = cache_get
        self._cache_put = cache_put

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(self, query: str, *, books: tuple[str, ...] = (), page: int | None = None) -> list[DorarResult]:
        key = cache_key(query, books, page)
        if self._cache_get is not None:
            try:
                cached = await self._cache_get(key)
            except Exception as e:  # noqa: BLE001 - a cache outage must not block the live source
                log.warning("dorar_cache_get_failed", extra={"error": type(e).__name__})
                cached = None
            if cached is not None:
                return [DorarResult(**r) for r in cached]

        params: dict[str, object] = {"skey": query}
        if books:
            params["s[]"] = list(books)
        if page and page > 1:
            params["page"] = page
        body = await get_json_with_retry(self._client, SOURCE, self.url, params=params, timeout_s=self.timeout_s)
        try:
            html = body["ahadith"]["result"]  # type: ignore[index]
        except (KeyError, TypeError) as e:
            raise SourceUnavailable(SOURCE, "unexpected_shape") from e
        if not isinstance(html, str):
            raise SourceUnavailable(SOURCE, "unexpected_shape")
        results = parse_results(html, query)

        if self._cache_put is not None:
            try:
                await self._cache_put(key, query, [asdict(r) for r in results])
            except Exception as e:  # noqa: BLE001
                log.warning("dorar_cache_put_failed", extra={"error": type(e).__name__})
        return results
