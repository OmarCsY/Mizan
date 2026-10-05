"""Extraction schema (SPEC §7.2, AMENDMENT 3, 7)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Claim(BaseModel):
    type: Literal["quran", "hadith", "attributed_saying"]
    span: str
    lang: str
    claimed_source: str | None
    ar_queries: list[str] = Field(min_length=1, max_length=3)


class Extraction(BaseModel):
    intent: Literal["claims", "no_claims", "evidence_request", "personal_ruling"]
    personal_ruling_request: bool
    claims: list[Claim]
