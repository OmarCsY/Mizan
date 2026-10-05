"""Embed every translation and Arabic hadith text that has no embedding yet (SPEC §6, TASKS B08).

    python scripts/embed_corpus.py                 # both tables
    python scripts/embed_corpus.py --table hadith_translations --limit 500

Batches of EMBEDDING_BATCH_SIZE (100); rows that already have an embedding are skipped, so the script is
resumable. Needs DATABASE_URL and EMBEDDING_* in the environment.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402

TABLES = {
    # table: (key columns, text column)
    "quran_translations": (("verse_id", "tr_key"), "text"),
    "hadith_translations": (("hadith_id", "lang"), "text"),
}


class Embedder(Protocol):
    async def embed_with_usage(self, texts: list[str], input_type: str = "document"): ...  # noqa: E704


async def embed_table(conn, embedder: Embedder, table: str, batch: int, limit: int | None = None) -> tuple[int, int]:
    """Embed rows of `table` with a null embedding. Returns (rows embedded, tokens used)."""
    from pgvector.asyncpg import register_vector

    await register_vector(conn)
    keys, text_col = TABLES[table]
    key_sql = ", ".join(keys)
    done = tokens = 0
    t0 = time.perf_counter()
    while limit is None or done < limit:
        n = batch if limit is None else min(batch, limit - done)
        rows = await conn.fetch(
            f"select {key_sql}, {text_col} from {table} where embedding is null order by {key_sql} limit $1", n
        )
        if not rows:
            break
        res = await embedder.embed_with_usage([r[text_col] for r in rows], "document")
        where = " and ".join(f"{k} = ${i + 2}" for i, k in enumerate(keys))
        await conn.executemany(
            f"update {table} set embedding = $1 where {where}",
            [(vec, *(r[k] for k in keys)) for vec, r in zip(res.vectors, rows, strict=True)],
        )
        done += len(rows)
        tokens += res.tokens
        print(f"  {table}: {done} embedded ({time.perf_counter() - t0:.0f} s, {tokens} tokens)", flush=True)
    return done, tokens


async def main_async(args: argparse.Namespace) -> int:
    import asyncpg

    from app.llm.embeddings import EmbeddingClient

    s = get_settings()
    if not s.database_url or not s.embedding_provider:
        print("DATABASE_URL and EMBEDDING_PROVIDER / EMBEDDING_API_KEY / EMBEDDING_MODEL must be set")
        return 2
    conn = await asyncpg.connect(s.database_url)
    try:
        embedder = EmbeddingClient(s)
        for table in [args.table] if args.table else list(TABLES):
            n, tok = await embed_table(conn, embedder, table, s.embedding_batch_size, args.limit)
            remaining = await conn.fetchval(f"select count(*) from {table} where embedding is null")
            print(f"{table}: embedded {n} rows ({tok} tokens); {remaining} rows still without embedding")
    finally:
        await conn.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", choices=list(TABLES))
    ap.add_argument("--limit", type=int, default=None)
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.exit(main())
