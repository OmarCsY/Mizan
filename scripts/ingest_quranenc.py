"""Ingest approved QuranEnc translations for the pinned keys (SPEC §6, TASKS B06).

    python scripts/ingest_quranenc.py             # fetch (cached under data/raw/quranenc) + upsert
    python scripts/ingest_quranenc.py --no-db     # fetch + validate only
    python scripts/ingest_quranenc.py --refresh   # ignore the local cache

Links each verse to quran_verses by (surah, ayah). Idempotent: unchanged rows are not rewritten; a
changed translation text is updated and its embedding reset so embed_corpus.py re-embeds it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import DATA_DIR, get_settings  # noqa: E402
from app.pipeline import quran_match  # noqa: E402
from app.pipeline.normalize import normalize_ar  # noqa: E402
from app.sources.quranenc import QuranEncClient, TranslatedAya  # noqa: E402

CACHE = DATA_DIR / "raw" / "quranenc"
N_SURAHS = 114
N_VERSES = 6236


async def fetch_key(client: QuranEncClient, key: str, refresh: bool, concurrency: int) -> list[TranslatedAya]:
    sem = asyncio.Semaphore(concurrency)
    (CACHE / key).mkdir(parents=True, exist_ok=True)

    async def one(surah: int) -> list[TranslatedAya]:
        path = CACHE / key / f"{surah:03d}.json"
        if path.exists() and not refresh:
            return [TranslatedAya(**r) for r in json.loads(path.read_text(encoding="utf-8"))]
        async with sem:
            rows = await client.sura(key, surah)
        path.write_text(json.dumps([asdict(r) for r in rows], ensure_ascii=False), encoding="utf-8")
        return rows

    out: list[TranslatedAya] = []
    for rows in await asyncio.gather(*(one(s) for s in range(1, N_SURAHS + 1))):
        out.extend(rows)
    return out


def validate(key: str, rows: list[TranslatedAya], index: quran_match.QuranIndex) -> list[tuple[int, str]]:
    """Map rows to verse ids; check count, coverage and Arabic alignment with our Mushaf."""
    linked: list[tuple[int, str]] = []
    seen: set[tuple[int, int]] = set()
    arabic_mismatch = 0
    for r in rows:
        v = index.verse(r.surah, r.ayah)
        if v is None:
            raise SystemExit(f"{key}: {r.surah}:{r.ayah} not in the Mushaf")
        if (r.surah, r.ayah) in seen:
            raise SystemExit(f"{key}: duplicate {r.surah}:{r.ayah}")
        seen.add((r.surah, r.ayah))
        if not r.translation.strip():
            raise SystemExit(f"{key}: empty translation at {r.surah}:{r.ayah}")
        if r.arabic_text and normalize_ar(r.arabic_text) != v.clean:
            arabic_mismatch += 1
        linked.append((v.id, r.translation.strip()))
    if len(linked) != N_VERSES:
        raise SystemExit(f"{key}: expected {N_VERSES} verses, got {len(linked)}")
    print(f"  {key}: {len(linked)} verses linked; QuranEnc arabic_text differs from our normalized Uthmani "
          f"for {arabic_mismatch} verses")
    return linked


async def upsert(dsn: str, key: str, lang: str, linked: list[tuple[int, str]]) -> tuple[int, int]:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        before = await conn.fetchval("select count(*) from quran_translations where tr_key = $1", key)
        await conn.executemany(
            """
            insert into quran_translations (verse_id, lang, tr_key, text) values ($1, $2, $3, $4)
            on conflict (verse_id, tr_key) do update set text = excluded.text, embedding = null
            where quran_translations.text is distinct from excluded.text
            """,
            [(vid, lang, key, text) for vid, text in linked],
        )
        after = await conn.fetchval("select count(*) from quran_translations where tr_key = $1", key)
        return before, after
    finally:
        await conn.close()


async def main_async(args: argparse.Namespace) -> int:
    s = get_settings()
    keys = {"en": s.quranenc_key_en, "ur": s.quranenc_key_ur}
    index = quran_match.load_from_json() if quran_match.QURAN_JSON.exists() else await quran_match.load_from_db()
    client = QuranEncClient()
    try:
        for lang, key in keys.items():
            rows = await fetch_key(client, key, args.refresh, s.external_max_concurrency)
            linked = validate(key, rows, index)
            if args.no_db:
                continue
            if not s.database_url:
                print("  DATABASE_URL not set: skipped upsert")
                continue
            before, after = await upsert(s.database_url, key, lang, linked)
            print(f"  {key}: quran_translations rows {before} -> {after} (inserted {after - before})")
    finally:
        await client.aclose()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.exit(main())
