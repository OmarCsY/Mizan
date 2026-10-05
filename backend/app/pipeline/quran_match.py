"""Verse matcher (SPEC §7.3). This module holds the in-memory Mushaf index (B05); matching is added in B11.

At startup the whole Mushaf (6,236 verses) is loaded into memory, plus windows of 2 and 3 consecutive
verses within the same surah, so quotes spanning verses can be matched. Each window has two matching
forms (DECISIONS D-3): normalized Uthmani and normalized imla'i.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import DATA_DIR
from app.core.logging import get_logger

log = get_logger(__name__)

QURAN_JSON = DATA_DIR / "quran.json"
WINDOW_SIZES = (1, 2, 3)


@dataclass(frozen=True, slots=True)
class Verse:
    id: int
    surah: int
    ayah: int
    uthmani: str
    clean: str
    imlaei_clean: str
    surah_name_ar: str
    surah_name_en: str


@dataclass(frozen=True, slots=True)
class Window:
    start: int  # index into QuranIndex.verses
    size: int  # number of consecutive verses (same surah)
    clean: str
    imlaei_clean: str


@dataclass
class QuranIndex:
    verses: list[Verse]
    windows: list[Window] = field(default_factory=list)
    by_ref: dict[tuple[int, int], int] = field(default_factory=dict)
    corpus_clean: list[str] = field(default_factory=list)  # aligned with windows
    corpus_imlaei: list[str] = field(default_factory=list)  # aligned with windows

    def __post_init__(self) -> None:
        self.by_ref = {(v.surah, v.ayah): i for i, v in enumerate(self.verses)}
        for size in WINDOW_SIZES:
            for i in range(len(self.verses) - size + 1):
                span = self.verses[i : i + size]
                if span[0].surah != span[-1].surah:
                    continue
                self.windows.append(
                    Window(
                        start=i,
                        size=size,
                        clean=" ".join(v.clean for v in span),
                        imlaei_clean=" ".join(v.imlaei_clean for v in span),
                    )
                )
        self.corpus_clean = [w.clean for w in self.windows]
        self.corpus_imlaei = [w.imlaei_clean for w in self.windows]

    def verse(self, surah: int, ayah: int) -> Verse | None:
        i = self.by_ref.get((surah, ayah))
        return self.verses[i] if i is not None else None

    @property
    def n_surahs(self) -> int:
        return len({v.surah for v in self.verses})


def load_from_json(path: Path = QURAN_JSON) -> QuranIndex:
    data = json.loads(path.read_text(encoding="utf-8"))
    return QuranIndex(
        [
            Verse(
                id=v["id"], surah=v["surah"], ayah=v["ayah"], uthmani=v["uthmani"], clean=v["clean"],
                imlaei_clean=v["imlaei_clean"], surah_name_ar=v["surah_name_ar"], surah_name_en=v["surah_name_en"],
            )
            for v in data["verses"]
        ]
    )


async def load_from_db() -> QuranIndex:
    from app.db.session import get_pool

    pool = await get_pool()
    rows = await pool.fetch(
        "select id, surah, ayah, text_uthmani, text_clean, text_imlaei_clean, surah_name_ar, surah_name_en "
        "from quran_verses order by id"
    )
    return QuranIndex(
        [
            Verse(
                id=r["id"], surah=r["surah"], ayah=r["ayah"], uthmani=r["text_uthmani"], clean=r["text_clean"],
                imlaei_clean=r["text_imlaei_clean"], surah_name_ar=r["surah_name_ar"] or "",
                surah_name_en=r["surah_name_en"] or "",
            )
            for r in rows
        ]
    )


_index: QuranIndex | None = None


async def load_index() -> QuranIndex | None:
    """Load data/quran.json if present, else quran_verses from the DB (DECISIONS D-10). Never raises."""
    global _index
    t0 = time.perf_counter()
    try:
        _index = load_from_json() if QURAN_JSON.exists() else await load_from_db()
    except Exception as e:  # noqa: BLE001 - the API still starts; checks report source_unavailable
        log.error("quran_index_load_failed", extra={"error": type(e).__name__})
        return None
    log.info(
        "quran_index_loaded",
        extra={
            "verses": len(_index.verses),
            "windows": len(_index.windows),
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        },
    )
    return _index


def get_index() -> QuranIndex | None:
    return _index
