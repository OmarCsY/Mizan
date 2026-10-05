"""Apply SQL migrations in backend/app/db/migrations in order, once each.

    python scripts/migrate.py                 # uses DATABASE_URL from env / .env
    python scripts/migrate.py --dsn postgres://...

Applied files are recorded in `schema_migrations`; each file runs in one transaction.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402

MIGRATIONS = ROOT / "backend" / "app" / "db" / "migrations"


async def migrate(dsn: str) -> list[str]:
    conn = await asyncpg.connect(dsn)
    applied_now: list[str] = []
    try:
        await conn.execute(
            "create table if not exists schema_migrations (name text primary key, applied_at timestamptz default now())"
        )
        done = {r["name"] for r in await conn.fetch("select name from schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute("insert into schema_migrations (name) values ($1)", path.name)
            applied_now.append(path.name)
    finally:
        await conn.close()
    return applied_now


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=None)
    args = ap.parse_args()
    dsn = args.dsn or get_settings().database_url
    if not dsn:
        print("DATABASE_URL is not set (see .env.example)")
        return 2
    applied = asyncio.run(migrate(dsn))
    print("applied: " + (", ".join(applied) if applied else "nothing (up to date)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
