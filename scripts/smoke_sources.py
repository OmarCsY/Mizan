"""Live smoke test of every external source (SPEC §4.3, task B02).

Calls each endpoint with one real request, prints latency and the actual field names, and saves one raw
response per call under backend/tests/fixtures/<source>/. Run again right before judging.

    python scripts/smoke_sources.py            # check + save fixtures
    python scripts/smoke_sources.py --no-save  # check only

No religious text is typed here: the Dorar query is taken from a HadeethEnc response, and Mushaf
fixture rows are selected by (surah, ayah) reference.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "backend" / "tests" / "fixtures"
RAW_DIR = ROOT / "data" / "raw"

QURANENC = "https://quranenc.com/api/v1"
HADEETHENC = "https://hadeethenc.com/api/v1"
DORAR_API = "https://dorar.net/dorar_api.json"
KFGQPC_ZIP_URL = "https://download.qurancomplex.gov.sa/resources_dev/kfgqpc_hafs_v30.zip"
KFGQPC_JSON_MEMBER = "kfgqpc_hafs_v30-data/kfgqpc_hafs_v30.json"
MCP_URL = "https://mcp.islamiccontent.org"

# Dorar book ids for the site/API `s[]` filter, confirmed by smoke test (see docs/SOURCES.md).
DORAR_BOOK_BUKHARI = "6216"
DORAR_BOOK_MUSLIM = "3088"

# Verses saved in the Mushaf sample fixture (references only, text comes from the data).
MUSHAF_SAMPLE_REFS = {(1, 1), (1, 2), (2, 153), (2, 255), (3, 2), (8, 46)}

TIMEOUT = httpx.Timeout(20.0)
HEADERS = {"User-Agent": "Mizan/0.1 (source verification research; contact via repo)"}
TASHKEEL = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")


@dataclass
class Check:
    source: str
    name: str
    ok: bool = False
    latency_ms: int | None = None
    fields: list[str] = field(default_factory=list)
    note: str = ""


def save(rel: str, content: str | bytes, enabled: bool) -> None:
    if not enabled:
        return
    path = FIXTURES / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


async def timed_get(client: httpx.AsyncClient, url: str, **kw: Any) -> tuple[httpx.Response, int]:
    t0 = time.perf_counter()
    r = await client.get(url, **kw)
    return r, int((time.perf_counter() - t0) * 1000)


def keys_of(obj: Any) -> list[str]:
    if isinstance(obj, list) and obj:
        obj = obj[0]
    return sorted(obj.keys()) if isinstance(obj, dict) else [type(obj).__name__]


async def check_quranenc(client: httpx.AsyncClient, save_on: bool) -> tuple[list[Check], dict[str, str]]:
    checks: list[Check] = []
    pinned: dict[str, str] = {}
    for lang in ("en", "ur"):
        c = Check("quranenc", f"translations/list/{lang}")
        try:
            r, c.latency_ms = await timed_get(client, f"{QURANENC}/translations/list/{lang}")
            r.raise_for_status()
            data = r.json()
            trs = data["translations"]
            c.fields = keys_of(trs)
            c.ok = bool(trs)
            c.note = "keys: " + ", ".join(t["key"] for t in trs)
            save(f"quranenc/translations_list_{lang}.json", r.text, save_on)
            keys = [t["key"] for t in trs]
            # Prefer Rowwad (en) and Junagarhi (ur) when the live list offers them (SPEC §4.2).
            preferred = {"en": "english_rwwad", "ur": "urdu_junagarhi"}[lang]
            pinned[lang] = preferred if preferred in keys else keys[0]
        except Exception as e:  # noqa: BLE001 - smoke test reports every failure
            c.note = repr(e)
        checks.append(c)

    if "en" in pinned:
        c = Check("quranenc", f"translation/sura/{pinned['en']}/1")
        try:
            r, c.latency_ms = await timed_get(client, f"{QURANENC}/translation/sura/{pinned['en']}/1")
            r.raise_for_status()
            res = r.json()["result"]
            c.fields, c.ok, c.note = keys_of(res), len(res) > 0, f"{len(res)} verses"
            save(f"quranenc/sura_{pinned['en']}_1.json", r.text, save_on)
        except Exception as e:  # noqa: BLE001
            c.note = repr(e)
        checks.append(c)
    if "ur" in pinned:
        c = Check("quranenc", f"translation/aya/{pinned['ur']}/2/255")
        try:
            r, c.latency_ms = await timed_get(client, f"{QURANENC}/translation/aya/{pinned['ur']}/2/255")
            r.raise_for_status()
            res = r.json()["result"]
            c.fields, c.ok = keys_of(res), bool(res.get("arabic_text"))
            save(f"quranenc/aya_{pinned['ur']}_2_255.json", r.text, save_on)
        except Exception as e:  # noqa: BLE001
            c.note = repr(e)
        checks.append(c)
    return checks, pinned


async def check_hadeethenc(client: httpx.AsyncClient, save_on: bool) -> tuple[list[Check], str | None]:
    checks: list[Check] = []
    dorar_query: str | None = None

    c = Check("hadeethenc", "categories/list?language=ar")
    try:
        r, c.latency_ms = await timed_get(client, f"{HADEETHENC}/categories/list/", params={"language": "ar"})
        r.raise_for_status()
        cats = r.json()
        roots = [x for x in cats if not x.get("parent_id")]
        c.fields, c.ok = keys_of(cats), bool(cats)
        c.note = f"{len(cats)} categories, {len(roots)} roots"
        save("hadeethenc/categories_list_ar.json", r.text, save_on)
        first_cat = roots[0]["id"] if roots else cats[0]["id"]
    except Exception as e:  # noqa: BLE001
        c.note = repr(e)
        checks.append(c)
        return checks, None
    checks.append(c)

    c = Check("hadeethenc", f"hadeeths/list?category_id={first_cat}")
    hid = None
    try:
        r, c.latency_ms = await timed_get(
            client,
            f"{HADEETHENC}/hadeeths/list/",
            params={"language": "ar", "category_id": first_cat, "page": 1, "per_page": 5},
        )
        r.raise_for_status()
        body = r.json()
        c.fields = sorted(body.keys()) + [f"data[].{k}" for k in keys_of(body["data"])]
        c.ok = bool(body["data"])
        c.note = f"meta={body.get('meta')}"
        hid = body["data"][0]["id"]
        save(f"hadeethenc/list_cat{first_cat}_p1.json", r.text, save_on)
    except Exception as e:  # noqa: BLE001
        c.note = repr(e)
    checks.append(c)

    if hid:
        for lang in ("ar", "en", "ur"):
            c = Check("hadeethenc", f"hadeeths/one?id={hid}&language={lang}")
            try:
                r, c.latency_ms = await timed_get(
                    client, f"{HADEETHENC}/hadeeths/one/", params={"id": hid, "language": lang}
                )
                r.raise_for_status()
                one = r.json()
                c.fields, c.ok = keys_of(one), bool(one.get("hadeeth"))
                save(f"hadeethenc/one_{hid}_{lang}.json", r.text, save_on)
                if lang == "ar":
                    dorar_query = TASHKEEL.sub("", one["title"]).strip()
            except Exception as e:  # noqa: BLE001
                c.note = repr(e)
            checks.append(c)

    c = Check("hadeethenc", "hadeeths/search (optional)")
    try:
        r, c.latency_ms = await timed_get(client, f"{HADEETHENC}/hadeeths/search/", params={"language": "ar", "q": "x"})
        c.ok = r.status_code == 200
        c.note = f"HTTP {r.status_code}; " + ("usable" if c.ok else "not usable -> local rapidfuzz fallback (SPEC §7.4 C)")
    except Exception as e:  # noqa: BLE001
        c.note = repr(e)
    c.ok = True  # optional endpoint: absence is an expected outcome, not a failure
    checks.append(c)
    return checks, dorar_query


async def check_dorar(client: httpx.AsyncClient, query: str | None, save_on: bool) -> list[Check]:
    from bs4 import BeautifulSoup

    checks: list[Check] = []
    if not query:
        return [Check("dorar", "dorar_api.json", note="no query (HadeethEnc failed)")]
    variants = [
        ("plain", {"skey": query}),
        ("page2", {"skey": query, "page": 2}),
        ("sahihayn", {"skey": query, "s[]": [DORAR_BOOK_BUKHARI, DORAR_BOOK_MUSLIM]}),
    ]
    for label, params in variants:
        c = Check("dorar", f"dorar_api.json ({label})")
        try:
            r, c.latency_ms = await timed_get(client, DORAR_API, params=params)
            r.raise_for_status()
            body = r.json()
            html = body["ahadith"]["result"]
            soup = BeautifulSoup(html, "lxml")
            n_h, n_i = len(soup.select("div.hadith")), len(soup.select("div.hadith-info"))
            subtitles = sorted({s.get_text(strip=True) for s in soup.select("span.info-subtitle")})
            books = [
                m.strip() for m in re.findall(r"المصدر:</span>\s*([^\n<]+)", html)
            ]
            c.fields = ["ahadith.result (HTML)"] + subtitles
            c.ok = n_h > 0 and n_h == n_i
            c.note = f"{n_h} results; books[:3]={books[:3]}; links={[a.get('href') for a in soup.find_all('a')][:2]}"
            save(f"dorar/api_{label}.json", r.text, save_on)
            save(
                f"dorar/api_{label}.meta.json",
                json.dumps({"params": params, "query_source": "HadeethEnc title of first hadith in first root category"},
                           ensure_ascii=False, indent=1),
                save_on,
            )
        except Exception as e:  # noqa: BLE001
            c.note = repr(e)
        checks.append(c)
    return checks


async def check_mushaf(client: httpx.AsyncClient, save_on: bool) -> list[Check]:
    c = Check("mushaf", "King Fahd Complex kfgqpc_hafs_v30.zip")
    try:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        local = RAW_DIR / "kfgqpc_hafs_v30.zip"
        t0 = time.perf_counter()
        if local.exists():
            blob = local.read_bytes()
            c.note = "cached zip; "
        else:
            r = await client.get(KFGQPC_ZIP_URL)
            r.raise_for_status()
            blob = r.content
            local.write_bytes(blob)
        c.latency_ms = int((time.perf_counter() - t0) * 1000)
        rows = json.loads(zipfile.ZipFile(io.BytesIO(blob)).read(KFGQPC_JSON_MEMBER).decode("utf-8-sig"))
        c.fields = keys_of(rows)
        c.ok = len(rows) == 6236 and all(r.get("aya_text_emlaey") for r in rows)
        c.note += f"{len(rows)} verses; imla'i field present in all: {c.ok}"
        sample = [r for r in rows if (int(r["sura_no"]), int(r["aya_no"])) in MUSHAF_SAMPLE_REFS]
        save("mushaf/kfgqpc_hafs_v30_sample.json", json.dumps(sample, ensure_ascii=False, indent=1), save_on)
    except Exception as e:  # noqa: BLE001
        c.note = repr(e)
    return [c]


async def check_mcp(client: httpx.AsyncClient) -> list[Check]:
    c = Check("mcp", "mcp.islamiccontent.org (optional, reachability only)")
    try:
        r, c.latency_ms = await timed_get(client, MCP_URL)
        c.note = f"HTTP {r.status_code}"
        c.ok = r.status_code < 500
    except Exception as e:  # noqa: BLE001
        c.note = repr(e)
    return [c]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()
    save_on = not args.no_save

    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True) as client:
        q_checks, pinned = await check_quranenc(client, save_on)
        h_checks, dorar_query = await check_hadeethenc(client, save_on)
        d_checks = await check_dorar(client, dorar_query, save_on)
        m_checks = await check_mushaf(client, save_on)
        x_checks = await check_mcp(client)

    all_checks = q_checks + h_checks + d_checks + m_checks + x_checks
    for c in all_checks:
        status = "PASS" if c.ok else "FAIL"
        lat = f"{c.latency_ms} ms" if c.latency_ms is not None else "-"
        print(f"[{status}] {c.source:10s} {c.name:45s} {lat:>8s}  {c.note}")
        if c.fields:
            print(f"           fields: {', '.join(c.fields)}")
    print(f"\nPinned QuranEnc keys: QURANENC_KEY_EN={pinned.get('en')}  QURANENC_KEY_UR={pinned.get('ur')}")

    required = [c for c in all_checks if c.source != "mcp"]
    by_source: dict[str, bool] = {}
    for c in required:
        by_source[c.source] = by_source.get(c.source, True) and c.ok
    print("\nPer source: " + "  ".join(f"{s}={'PASS' if ok else 'FAIL'}" for s, ok in by_source.items()))
    return 0 if all(by_source.values()) else 1


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.exit(asyncio.run(main()))
