"""Ingest the Mushaf (SPEC §6, TASKS B05, DECISIONS D-3 / D-10).

Source: King Fahd Complex developer data, `kfgqpc_hafs_v30.zip` (JSON member). Each row carries both the
Uthmani text (`aya_text_unicode`, display + matching form a) and the imla'i text (`aya_text_emlaey`,
matching form b), so (surah, ayah) alignment between the two forms is 1:1 by construction.

    python scripts/ingest_quran.py            # write data/quran.json and upsert quran_verses (if DATABASE_URL)
    python scripts/ingest_quran.py --no-db    # only data/quran.json (e.g. in a deploy build step)

The zip and data/quran.json are gitignored (no explicit license on the source, D-10).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import DATA_DIR, get_settings  # noqa: E402
from app.pipeline.normalize import normalize_ar  # noqa: E402

ZIP_URL = "https://download.qurancomplex.gov.sa/resources_dev/kfgqpc_hafs_v30.zip"
ZIP_PATH = DATA_DIR / "raw" / "kfgqpc_hafs_v30.zip"
JSON_MEMBER = "kfgqpc_hafs_v30-data/kfgqpc_hafs_v30.json"
OUT_PATH = DATA_DIR / "quran.json"
N_VERSES = 6236
N_SURAHS = 114
SOURCE = "King Fahd Complex kfgqpc_hafs_v30 (qurancomplex.gov.sa/quran-dev)"

# End-of-ayah sign U+06DD followed by the verse number in Arabic-Indic digits.
_AYAH_END = re.compile(r"\s*۝[٠-٩]+\s*$")
_TASHKEEL_FOR_NAMES = re.compile(r"[ً-ٰٟ]")


def load_rows() -> list[dict]:
    if not ZIP_PATH.exists():
        ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {ZIP_URL}")
        r = httpx.get(ZIP_URL, timeout=120, follow_redirects=True)
        r.raise_for_status()
        ZIP_PATH.write_bytes(r.content)
    blob = ZIP_PATH.read_bytes()
    return json.loads(zipfile.ZipFile(io.BytesIO(blob)).read(JSON_MEMBER).decode("utf-8-sig"))


def build(rows: list[dict]) -> list[dict]:
    if len(rows) != N_VERSES:
        raise SystemExit(f"expected {N_VERSES} verses, got {len(rows)}")
    verses: list[dict] = []
    prev = (1, 0)
    for i, r in enumerate(rows, start=1):
        surah, ayah, vid = int(r["sura_no"]), int(r["aya_no"]), int(r["id"])
        if vid != i:
            raise SystemExit(f"row {i}: id {vid} out of order")
        # contiguous: next ayah in the same surah, or ayah 1 of the next surah
        if not ((surah == prev[0] and ayah == prev[1] + 1) or (surah == prev[0] + 1 and ayah == 1)):
            raise SystemExit(f"row {i}: {surah}:{ayah} does not follow {prev[0]}:{prev[1]}")
        prev = (surah, ayah)
        uthmani = _AYAH_END.sub("", r["aya_text_unicode"]).strip()
        imlaei = r["aya_text_emlaey"].strip()
        clean, imlaei_clean = normalize_ar(uthmani), normalize_ar(imlaei)
        if not clean or not imlaei_clean:
            raise SystemExit(f"{surah}:{ayah}: empty text after normalization")
        verses.append(
            {
                "id": vid,
                "surah": surah,
                "ayah": ayah,
                "uthmani": uthmani,
                "clean": clean,
                "imlaei": imlaei,
                "imlaei_clean": imlaei_clean,
                "surah_name_ar": _TASHKEEL_FOR_NAMES.sub("", r["sura_name_ar"]).strip(),
                "surah_name_en": r["sura_name_en"].strip(),
            }
        )
    if prev[0] != N_SURAHS:
        raise SystemExit(f"expected {N_SURAHS} surahs, last is {prev[0]}")
    return verses


async def upsert_db(verses: list[dict], dsn: str) -> int:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        await conn.executemany(
            """
            insert into quran_verses (id, surah, ayah, text_uthmani, text_clean, text_imlaei_clean,
                                      surah_name_ar, surah_name_en)
            values ($1, $2, $3, $4, $5, $6, $7, $8)
            on conflict (id) do update set
              text_uthmani = excluded.text_uthmani, text_clean = excluded.text_clean,
              text_imlaei_clean = excluded.text_imlaei_clean,
              surah_name_ar = excluded.surah_name_ar, surah_name_en = excluded.surah_name_en
            """,
            [
                (v["id"], v["surah"], v["ayah"], v["uthmani"], v["clean"], v["imlaei_clean"],
                 v["surah_name_ar"], v["surah_name_en"])
                for v in verses
            ],
        )
        return await conn.fetchval("select count(*) from quran_verses")
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-db", action="store_true")
    args = ap.parse_args()

    verses = build(load_rows())
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps({"source": SOURCE, "verses": verses}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"wrote {OUT_PATH} ({len(verses)} verses, {len({v['surah'] for v in verses})} surahs)")

    by_ref = {(v["surah"], v["ayah"]): v for v in verses}
    for ref in ((1, 1), (2, 255), (114, 6)):  # spot-check (printed from the data)
        v = by_ref[ref]
        print(f"  {ref[0]}:{ref[1]} [{v['surah_name_en']}] uthmani: {v['uthmani']}")
        print(f"  {' ' * len(f'{ref[0]}:{ref[1]}')}  imlaei_clean: {v['imlaei_clean']}")
    same = sum(v["clean"] == v["imlaei_clean"] for v in verses)
    print(f"  normalized Uthmani == normalized imla'i for {same}/{len(verses)} verses (rest differ in rasm)")

    dsn = get_settings().database_url
    if args.no_db:
        return 0
    if not dsn:
        print("DATABASE_URL not set: skipped quran_verses upsert (data/quran.json is enough for the server)")
        return 0
    n = asyncio.run(upsert_db(verses, dsn))
    print(f"quran_verses rows: {n}")
    return 0 if n == N_VERSES else 1


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.exit(main())
