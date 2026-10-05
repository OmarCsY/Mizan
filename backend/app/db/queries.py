"""SQL used by the API, pipeline and scripts. All functions take the pool explicitly or fetch it."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

from app.db.session import get_pool


async def delete_expired_results(pool: asyncpg.Pool | None = None) -> int:
    pool = pool or await get_pool()
    status = await pool.execute("delete from check_results where expires_at < now()")
    return int(status.split()[-1])


async def save_check_result(check_id: str, result: dict[str, Any], expires_at: datetime) -> None:
    pool = await get_pool()
    await pool.execute(
        "insert into check_results (check_id, result, expires_at) values ($1, $2, $3) "
        "on conflict (check_id) do update set result = excluded.result, expires_at = excluded.expires_at",
        check_id, result, expires_at,
    )


async def get_check_result(check_id: str) -> dict[str, Any] | None:
    pool = await get_pool()
    row = await pool.fetchrow(
        "select result, reply, expires_at from check_results where check_id = $1 and expires_at > now()", check_id
    )
    return dict(row) if row else None


async def dorar_cache_get(query_hash: str) -> list[dict[str, Any]] | None:
    pool = await get_pool()
    return await pool.fetchval("select response from dorar_cache where query_hash = $1", query_hash)


async def dorar_cache_put(query_hash: str, query: str, response: list[dict[str, Any]]) -> None:
    pool = await get_pool()
    await pool.execute(
        "insert into dorar_cache (query_hash, query, response) values ($1, $2, $3) "
        "on conflict (query_hash) do update set response = excluded.response, fetched_at = now()",
        query_hash, query, response,
    )


async def insert_check_metrics(
    *,
    check_id: str,
    channel: str,
    lang: str | None,
    n_claims: int,
    status: str,
    latency_ms: int,
    llm_tokens_in: int,
    llm_tokens_out: int,
    embed_tokens: int,
    llm_providers: dict[str, int],
) -> None:
    """Per-check telemetry (SPEC §5, §12). No message text."""
    pool = await get_pool()
    await pool.execute(
        """
        insert into check_metrics (check_id, channel, lang, n_claims, status, latency_ms, llm_tokens_in,
                                   llm_tokens_out, embed_tokens, llm_providers)
        values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        on conflict (check_id) do nothing
        """,
        check_id, channel, lang, n_claims, status, latency_ms, llm_tokens_in, llm_tokens_out, embed_tokens,
        llm_providers,
    )
