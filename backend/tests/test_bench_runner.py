"""bench/run.py: resume, quota stop, error retry, rate limit (DECISIONS D-17). Uses a fake system."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.core.config import REPO_ROOT
from app.core.ratelimit import WindowLimiter
from app.llm.client import LLMQuotaExceeded

spec = importlib.util.spec_from_file_location("bench_run", REPO_ROOT / "bench" / "run.py")
bench_run = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["bench_run"] = bench_run
spec.loader.exec_module(bench_run)  # type: ignore[union-attr]

ITEMS = [{"id": f"b-{i:04d}", "text": f"item {i}", "split": "test"} for i in range(5)]


def lines(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


async def test_resume_skips_items_already_in_results(tmp_path: Path) -> None:
    out = tmp_path / "fake-test-1.jsonl"
    seen: list[str] = []

    async def system(item: dict) -> dict:
        seen.append(item["id"])
        if item["id"] == "b-0003":
            raise LLMQuotaExceeded("daily quota")
        return {"status": "ok"}

    with pytest.raises(bench_run.QuotaStop):
        await bench_run.run_one("fake", system, ITEMS, out, run=1, rpm=0)
    assert [r["id"] for r in lines(out)] == ["b-0000", "b-0001", "b-0002"]  # unfinished item not written

    seen.clear()

    async def system_ok(item: dict) -> dict:
        seen.append(item["id"])
        return {"status": "ok"}

    counts = await bench_run.run_one("fake", system_ok, ITEMS, out, run=1, rpm=0)
    assert seen == ["b-0003", "b-0004"]  # resumed after the quota reset
    assert counts == {"done": 2, "skipped": 3, "errors": 0}
    assert [r["id"] for r in lines(out)] == [f"b-{i:04d}" for i in range(5)]


async def test_errors_are_recorded_and_can_be_retried(tmp_path: Path) -> None:
    out = tmp_path / "fake-test-1.jsonl"

    async def flaky(item: dict) -> dict:
        if item["id"] == "b-0001":
            raise RuntimeError("source down")
        return {"status": "ok"}

    counts = await bench_run.run_one("fake", flaky, ITEMS, out, run=1, rpm=0)
    assert counts["errors"] == 1
    assert next(r for r in lines(out) if r["id"] == "b-0001")["error"].startswith("RuntimeError")

    async def ok(item: dict) -> dict:
        return {"status": "ok"}

    assert (await bench_run.run_one("fake", ok, ITEMS, out, run=1, rpm=0))["done"] == 0  # plain resume: all done
    counts = await bench_run.run_one("fake", ok, ITEMS, out, run=1, rpm=0, retry_errors=True)
    assert counts["done"] == 1  # only the failed item ran again


async def test_truncated_last_line_is_rerun(tmp_path: Path) -> None:
    out = tmp_path / "fake-test-1.jsonl"
    out.write_text(json.dumps({"id": "b-0000", "error": None}) + "\n" + '{"id": "b-0001", "outp', encoding="utf-8")
    assert bench_run.load_done(out, retry_errors=False) == {"b-0000"}


async def test_rate_limit_is_applied_per_item(tmp_path: Path) -> None:
    now = [0.0]

    async def fake_sleep(s: float) -> None:
        now[0] += s

    limiter = WindowLimiter(2, 60.0, clock=lambda: now[0], sleep=fake_sleep)

    async def ok(item: dict) -> dict:
        return {}

    await bench_run.run_one("fake", ok, ITEMS, tmp_path / "r.jsonl", run=1, rpm=2, limiter=limiter)
    assert now[0] >= 120.0  # 5 items at 2 per minute -> at least two full windows of waiting


async def test_unbuilt_system_reports_not_ready(tmp_path: Path) -> None:
    with pytest.raises(bench_run.SystemNotReady):
        await bench_run.run_one("llm_baseline", bench_run.system_llm_baseline, ITEMS, tmp_path / "x.jsonl",
                                run=1, rpm=0)
