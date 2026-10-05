from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.db import session

router = APIRouter()


@router.get("/health")
async def health() -> JSONResponse:
    """Liveness; queries the DB so the uptime pinger also keeps Supabase awake (SPEC §14, AMENDMENT 8)."""
    db_ok = await session.ping()
    return JSONResponse({"ok": db_ok, "db": db_ok}, status_code=200 if db_ok else 503)
