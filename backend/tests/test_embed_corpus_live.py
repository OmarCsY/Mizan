"""embed_corpus.py SQL path against a real (local or Supabase) DB with a stub embedder. Run with --live.

Uses a deterministic fake 1024-dim vector so no embedding key is needed; the real semantic check
(B08 AC: sensible match_verse / match_hadith results) runs once EMBEDDING_* keys exist.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from dataclasses import dataclass

import asyncpg
import pytest

from app.core.config import REPO_ROOT, get_settings

pytestmark = pytest.mark.live

spec = importlib.util.spec_from_file_location("embed_corpus", REPO_ROOT / "scripts" / "embed_corpus.py")
embed_corpus = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["embed_corpus"] = embed_corpus
spec.loader.exec_module(embed_corpus)  # type: ignore[union-attr]

DIM = 1024


@dataclass
class Res:
    vectors: list[list[float]]
    tokens: int


class StubEmbedder:
    async def embed_with_usage(self, texts: list[str], input_type: str = "document") -> Res:
        out = []
        for t in texts:
            seed = hashlib.sha1(t.encode("utf-8")).digest()
            out.append([seed[i % len(seed)] / 255.0 + 1e-3 for i in range(DIM)])
        return Res(out, len(texts))


async def test_embed_and_match_verse_roundtrip() -> None:
    dsn = get_settings().database_url
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        if not await conn.fetchval("select count(*) from quran_translations"):
            pytest.skip("quran_translations empty: run scripts/ingest_quranenc.py")
        tr = conn.transaction()  # rolled back below: leaves the DB as it was
        await tr.start()
        try:
            n, tok = await embed_corpus.embed_table(conn, StubEmbedder(), "quran_translations", batch=100, limit=200)
            assert n == 200 and tok == 200
            row = await conn.fetchrow(
                "select verse_id, tr_key, lang, text from quran_translations where embedding is not null "
                "order by verse_id limit 1"
            )
            q = (await StubEmbedder().embed_with_usage([row["text"]])).vectors[0]
            hits = await conn.fetch("select * from match_verse($1, $2, 3)", q, row["lang"])
            assert hits[0]["verse_id"] == row["verse_id"] and hits[0]["score"] > 0.999
        finally:
            await tr.rollback()
    finally:
        await conn.close()


class QuotaAfterFirstBatch(StubEmbedder):
    def __init__(self) -> None:
        self.calls = 0

    async def embed_with_usage(self, texts: list[str], input_type: str = "document") -> Res:
        from app.llm.embeddings import EmbeddingRateLimited

        self.calls += 1
        if self.calls > 1:
            raise EmbeddingRateLimited(60, daily=True)
        return await super().embed_with_usage(texts, input_type)


async def test_daily_quota_stops_and_next_run_resumes() -> None:
    dsn = get_settings().database_url
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        if not await conn.fetchval("select count(*) from quran_translations"):
            pytest.skip("quran_translations empty")
        tr = conn.transaction()
        await tr.start()
        try:
            before = await conn.fetchval("select count(*) from quran_translations where embedding is null")
            with pytest.raises(embed_corpus.QuotaStop):
                await embed_corpus.embed_table(conn, QuotaAfterFirstBatch(), "quran_translations", batch=50, limit=200)
            after_stop = await conn.fetchval("select count(*) from quran_translations where embedding is null")
            assert before - after_stop == 50  # first batch kept
            first_missing = await conn.fetchval(
                "select min(verse_id) from quran_translations where embedding is null and tr_key = 'english_rwwad'"
            )
            n, _ = await embed_corpus.embed_table(conn, StubEmbedder(), "quran_translations", batch=50, limit=50)
            assert n == 50
            # resumed at the first row without an embedding, not from the start
            assert await conn.fetchval(
                "select embedding is not null from quran_translations where verse_id = $1 and tr_key = 'english_rwwad'",
                first_missing,
            )
        finally:
            await tr.rollback()
    finally:
        await conn.close()
