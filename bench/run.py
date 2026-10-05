"""Mizan-Bench runner (SPEC §11.3, TASKS B19), resumable and rate-limited for free tiers (DECISIONS D-17).

    python bench/run.py --system mizan --split test --runs 3
    python bench/run.py --system llm_baseline --split test --runs 3 --rpm 8
    python bench/run.py --system dorar_direct --split test --runs 1 --retry-errors

Outputs go to bench/results/<system>-<split>-<run>.jsonl, one JSON line per item, appended and flushed
as soon as each item finishes. Items already present in a run's file are skipped, so a run that stops
(quota reset, Ctrl-C, crash) continues where it left off when started again. `--rpm` caps items per
minute. A provider quota error stops the run cleanly (exit code 3) without writing the unfinished item.
Other per-item errors are recorded with `"error"`; `--retry-errors` runs those items again.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.ratelimit import WindowLimiter  # noqa: E402

ITEMS = ROOT / "bench" / "items.jsonl"
RESULTS = ROOT / "bench" / "results"
DEFAULT_RPM = 10  # free-tier friendly; each mizan item makes 1 + (claims) LLM calls

System = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class QuotaStop(Exception):
    """The provider's quota is exhausted: stop now and resume after the reset."""


class SystemNotReady(Exception):
    pass


# --------------------------------------------------------------------------- systems


async def system_mizan(item: dict[str, Any]) -> dict[str, Any]:
    try:
        from app.pipeline.orchestrator import run_check  # B16
    except ImportError as e:
        raise SystemNotReady("mizan: orchestrator not built yet (TASKS B16)") from e
    result = await run_check(item["text"], channel="api")
    return result.model_dump(mode="json") if hasattr(result, "model_dump") else result


async def system_llm_baseline(item: dict[str, Any]) -> dict[str, Any]:
    raise SystemNotReady("llm_baseline: built in TASKS B19 (SPEC §11.4)")


async def system_dorar_direct(item: dict[str, Any]) -> dict[str, Any]:
    raise SystemNotReady("dorar_direct: built in TASKS B19 (SPEC §11.4)")


SYSTEMS: dict[str, System] = {
    "mizan": system_mizan,
    "llm_baseline": system_llm_baseline,
    "dorar_direct": system_dorar_direct,
}


def is_quota_error(e: BaseException) -> bool:
    from app.llm.client import LLMQuotaExceeded
    from app.llm.embeddings import EmbeddingRateLimited

    return isinstance(e, (LLMQuotaExceeded, QuotaStop)) or (isinstance(e, EmbeddingRateLimited) and e.daily)


# --------------------------------------------------------------------------- runner


def load_items(path: Path, split: str | None) -> list[dict[str, Any]]:
    items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [it for it in items if split in (None, "all") or it.get("split") == split]


def load_done(out: Path, retry_errors: bool) -> set[str]:
    if not out.exists():
        return set()
    done: set[str] = set()
    for line in out.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # a line cut by a crash: that item runs again
        if retry_errors and rec.get("error"):
            continue
        done.add(rec["id"])
    return done


async def run_one(
    system_name: str,
    system: System,
    items: list[dict[str, Any]],
    out: Path,
    *,
    run: int,
    rpm: int,
    retry_errors: bool = False,
    limiter: WindowLimiter | None = None,
) -> dict[str, int]:
    """Run `system` over `items`, appending to `out`. Returns counts {done, skipped, errors}."""
    out.parent.mkdir(parents=True, exist_ok=True)
    done = load_done(out, retry_errors)
    todo = [it for it in items if it["id"] not in done]
    limiter = limiter or WindowLimiter(rpm)
    counts = {"done": 0, "skipped": len(items) - len(todo), "errors": 0}
    print(f"[{out.name}] {len(todo)} to run, {counts['skipped']} already done", flush=True)
    with out.open("a", encoding="utf-8") as f:
        for it in todo:
            await limiter.acquire(1)
            t0 = time.perf_counter()
            rec: dict[str, Any] = {"id": it["id"], "system": system_name, "run": run}
            try:
                rec["output"] = await system(it)
                rec["error"] = None
            except SystemNotReady:
                raise
            except Exception as e:  # noqa: BLE001 - one item's failure must not stop the bench
                if is_quota_error(e):
                    raise QuotaStop(str(e)) from e
                rec["output"], rec["error"] = None, f"{type(e).__name__}: {e}"
                counts["errors"] += 1
            rec["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            rec["ts"] = datetime.now(timezone.utc).isoformat()
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            counts["done"] += 1
    return counts


async def main_async(args: argparse.Namespace) -> int:
    system = SYSTEMS[args.system]
    items = load_items(Path(args.items), args.split)
    if not items:
        print(f"no items for split {args.split!r} in {args.items}")
        return 2
    limiter = WindowLimiter(args.rpm)  # shared across runs: the provider quota is shared too
    for run in range(1, args.runs + 1):
        out = Path(args.out_dir) / f"{args.system}-{args.split}-{run}.jsonl"
        try:
            counts = await run_one(args.system, system, items, out, run=run, rpm=args.rpm,
                                   retry_errors=args.retry_errors, limiter=limiter)
        except QuotaStop as e:
            print(f"STOPPED on provider quota ({e}). Re-run the same command after the quota resets; "
                  "finished items are kept and skipped.")
            return 3
        except SystemNotReady as e:
            print(f"system not ready: {e}")
            return 2
        print(f"[{out.name}] done={counts['done']} skipped={counts['skipped']} errors={counts['errors']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", choices=list(SYSTEMS), required=True)
    ap.add_argument("--split", default="test", help="dev | test | all")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--rpm", type=int, default=DEFAULT_RPM, help="max items per minute (0 = unlimited)")
    ap.add_argument("--items", default=str(ITEMS))
    ap.add_argument("--out-dir", default=str(RESULTS))
    ap.add_argument("--retry-errors", action="store_true", help="re-run items whose stored result is an error")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.exit(main())
