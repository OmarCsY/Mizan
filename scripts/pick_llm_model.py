"""Pick the best Flash-class model available to the configured LLM key (DECISIONS D-15).

    python scripts/pick_llm_model.py           # list + probe, print the recommendation
    python scripts/pick_llm_model.py --write   # also set LLM_MODEL_EXTRACT/VERIFY/REPLY in .env

Lists `{LLM_BASE_URL}/models`, keeps text Flash models (not image/audio/tts/live/lite), orders them newest
first (stable before preview), and sends each one tiny JSON request until one answers 200. Flash-Lite is
tried only if no full Flash model works. Each probe costs a few tokens of free-tier quota.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import REPO_ROOT, get_settings  # noqa: E402

EXCLUDE = ("image", "audio", "tts", "live", "transcribe", "translate", "omni", "robotics", "computer", "embedding")
PROBE = {
    "temperature": 0,
    "response_format": {"type": "json_object"},
    "max_tokens": 100,
    "messages": [
        {"role": "system", "content": 'Return JSON {"ok": true} only.'},
        {"role": "user", "content": "<message>ping</message>"},
    ],
}


def version_key(model: str) -> tuple:
    m = re.search(r"gemini-(\d+)(?:\.(\d+))?", model)
    major, minor = (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)
    return (major, minor, "preview" not in model and "latest" not in model)


def candidates(ids: list[str]) -> tuple[list[str], list[str]]:
    names = [i.removeprefix("models/") for i in ids]
    text_flash = [n for n in names if "flash" in n and not any(x in n for x in EXCLUDE)]
    full = sorted([n for n in text_flash if "lite" not in n], key=version_key, reverse=True)
    lite = sorted([n for n in text_flash if "lite" in n], key=version_key, reverse=True)
    return full, lite


def write_env(model: str) -> None:
    env = REPO_ROOT / ".env"
    lines = env.read_text(encoding="utf-8").splitlines()
    keys = {"LLM_MODEL_EXTRACT", "LLM_MODEL_VERIFY", "LLM_MODEL_REPLY"}
    out, seen = [], set()
    for line in lines:
        k = line.split("=", 1)[0].strip()
        if k in keys:
            out.append(f"{k}={model}")
            seen.add(k)
        else:
            out.append(line)
    out += [f"{k}={model}" for k in sorted(keys - seen)]
    env.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote LLM_MODEL_EXTRACT/VERIFY/REPLY={model} to .env")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    s = get_settings()
    base, headers = s.llm_base_url.rstrip("/"), {"Authorization": f"Bearer {s.llm_api_key}"}
    with httpx.Client(timeout=40) as c:
        r = c.get(f"{base}/models", headers=headers)
        r.raise_for_status()
        full, lite = candidates([m["id"] for m in r.json().get("data", [])])
        print(f"Flash candidates: {full}\nFlash-Lite fallback: {lite}")
        for model in full + lite:
            t0 = time.perf_counter()
            resp = c.post(f"{base}/chat/completions", headers=headers, json={**PROBE, "model": model})
            dt = time.perf_counter() - t0
            if resp.status_code == 200:
                print(f"OK   {model} ({dt:.1f} s)")
                if args.write:
                    write_env(model)
                print(f"recommended: {model}")
                return 0
            body = resp.json() if "json" in resp.headers.get("content-type", "") else {}
            body = body[0] if isinstance(body, list) and body else body  # Gemini wraps errors in a list
            msg = body.get("error", {}).get("message", "") if isinstance(body, dict) else ""
            print(f"FAIL {model}: HTTP {resp.status_code} {msg[:110]}")
            if resp.status_code == 402:  # billing state of the whole project: every model will fail the same way
                print("HTTP 402 is project-level (prepaid billing without credits). Use a key from an AI Studio "
                      "project without billing enabled (free tier), or add credits.")
                return 1
    print("no Flash-class model answered; check the key / project billing state")
    return 1


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.exit(main())
