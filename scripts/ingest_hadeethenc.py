"""Ingest HadeethEnc hadiths in ar, en, ur (SPEC §6, TASKS B07).

    python scripts/ingest_hadeethenc.py          # priority categories (creed, virtues & manners, worship)
    python scripts/ingest_hadeethenc.py --all    # every root category
    python scripts/ingest_hadeethenc.py --no-db  # fetch + cache only

Categories -> hadith IDs (deduplicated) -> each hadith in ar, en, ur (en/ur only where the source lists
that translation). Responses are cached under data/raw/hadeethenc/, so the script is resumable and
re-runnable; DB writes are upserts. Concurrency 4 (EXTERNAL_MAX_CONCURRENCY), one retry with backoff.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import DATA_DIR, get_settings  # noqa: E402
from app.pipeline.normalize import normalize_ar  # noqa: E402
from app.sources import SourceUnavailable  # noqa: E402
from app.sources.hadeethenc import Hadeeth, HadeethEncClient, hadith_url  # noqa: E402

CACHE = DATA_DIR / "raw" / "hadeethenc"
# Priority (SPEC §6): creed (root 3), virtues & manners (root 5), worship (فقه العبادات, 121 under root 4).
PRIORITY_CATEGORIES = (3, 5, 121)
LANGS = ("ar", "en", "ur")


async def collect_ids(client: HadeethEncClient, category_ids: list[int]) -> list[int]:
    ids: dict[int, None] = {}
    for cid in category_ids:
        page, last = 1, 1
        n0 = len(ids)
        while page <= last:
            items, last = await client.list_page(cid, page)
            for it in items:
                ids.setdefault(int(it["id"]), None)
            page += 1
        print(f"  category {cid}: +{len(ids) - n0} new ids (total {len(ids)})", flush=True)
    return list(ids)


async def fetch_all(client: HadeethEncClient, ids: list[int], concurrency: int) -> dict[int, dict[str, Hadeeth]]:
    sem = asyncio.Semaphore(concurrency)
    out: dict[int, dict[str, Hadeeth]] = {}
    failures: list[tuple[int, str]] = []
    done = 0
    t0 = time.perf_counter()

    async def get(hid: int, lang: str) -> Hadeeth:
        path = CACHE / f"{hid}_{lang}.json"
        if path.exists():
            return Hadeeth(**{**json.loads(path.read_text(encoding="utf-8")), "translations": tuple()})
        async with sem:
            h = await client.one(hid, lang)
        path.write_text(json.dumps(asdict(h), ensure_ascii=False), encoding="utf-8")
        return h

    async def one(hid: int) -> None:
        nonlocal done
        try:
            ar = await get(hid, "ar")
            meta_path = CACHE / f"{hid}_langs.json"
            if ar.translations:
                meta_path.write_text(json.dumps(list(ar.translations)), encoding="utf-8")
            available = set(json.loads(meta_path.read_text(encoding="utf-8"))) if meta_path.exists() else set()
            by_lang = {"ar": ar}
            for lang in ("en", "ur"):
                if lang in available:
                    by_lang[lang] = await get(hid, lang)
            out[hid] = by_lang
        except SourceUnavailable as e:
            failures.append((hid, e.reason))
        done += 1
        if done % 200 == 0 or done == len(ids):
            print(f"  fetched {done}/{len(ids)} ({time.perf_counter() - t0:.0f} s, {len(failures)} failures)", flush=True)

    CACHE.mkdir(parents=True, exist_ok=True)
    await asyncio.gather(*(one(h) for h in ids))
    if failures:
        print(f"  {len(failures)} hadiths failed (re-run to retry): {failures[:5]}")
    return out


async def upsert(dsn: str, data: dict[int, dict[str, Hadeeth]]) -> dict[str, int]:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        hadith_rows, tr_rows = [], []
        for hid, by_lang in data.items():
            ar = by_lang["ar"]
            if not ar.text:
                continue
            hadith_rows.append((hid, ar.text, normalize_ar(ar.text), ar.attribution or None, ar.grade or None,
                                hadith_url(hid, "ar")))
            for lang, h in by_lang.items():
                if h.text:
                    tr_rows.append((hid, lang, h.text, h.explanation or None))
        async with conn.transaction():
            await conn.executemany(
                """
                insert into hadiths (id, text_ar, text_ar_clean, attribution, grade, url)
                values ($1, $2, $3, $4, $5, $6)
                on conflict (id) do update set text_ar = excluded.text_ar, text_ar_clean = excluded.text_ar_clean,
                  attribution = excluded.attribution, grade = excluded.grade, url = excluded.url
                """,
                hadith_rows,
            )
            await conn.executemany(
                """
                insert into hadith_translations (hadith_id, lang, text, explanation) values ($1, $2, $3, $4)
                on conflict (hadith_id, lang) do update set text = excluded.text, explanation = excluded.explanation,
                  embedding = null
                where hadith_translations.text is distinct from excluded.text
                """,
                tr_rows,
            )
        counts = {"hadiths": await conn.fetchval("select count(*) from hadiths")}
        for lang in LANGS:
            counts[lang] = await conn.fetchval("select count(*) from hadith_translations where lang = $1", lang)
        return counts
    finally:
        await conn.close()


async def main_async(args: argparse.Namespace) -> int:
    s = get_settings()
    client = HadeethEncClient()
    try:
        if args.all:
            cats = await client.categories("ar")
            category_ids = [int(c["id"]) for c in cats if not c.get("parent_id")]
        else:
            category_ids = list(PRIORITY_CATEGORIES)
        print(f"categories: {category_ids}")
        ids = await collect_ids(client, category_ids)
        data = await fetch_all(client, ids, s.external_max_concurrency)
    finally:
        await client.aclose()
    per_lang = {lang: sum(1 for d in data.values() if lang in d) for lang in LANGS}
    print(f"fetched {len(data)} hadiths; per language: {per_lang}")
    if args.no_db or not s.database_url:
        if not args.no_db:
            print("DATABASE_URL not set: skipped upsert")
        return 0
    counts = await upsert(s.database_url, data)
    print(f"DB counts: {counts}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="all root categories, not only the priority ones")
    ap.add_argument("--no-db", action="store_true")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.exit(main())
