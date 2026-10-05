"""Provider-agnostic LLM client returning schema-validated JSON (SPEC §2, TASKS B03).

`complete_json(prompt_name, variables, schema, model)` loads `prompts/<name>.txt` ("SYSTEM:" / "USER:"
sections), fills `{variables}` in a single pass (so user text is never re-scanned for placeholders),
calls the provider chosen by `LLM_PROVIDER`, validates the JSON against `schema` with pydantic,
retries once on invalid JSON, and returns `(parsed, usage)`.

Providers:
- `anthropic`: official SDK, structured outputs (`output_config.format` = JSON schema). Current Claude
  models reject sampling parameters, so temperature is not sent (DECISIONS D-12); determinism comes from
  the schema constraint and a fixed low effort.
- `openai_compatible`: `POST {LLM_BASE_URL}/chat/completions`, `temperature=0`,
  `response_format={"type": "json_object"}`, schema appended to the system prompt.
"""

from __future__ import annotations

import json
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

import anthropic
import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.retry import with_retry

log = get_logger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_SCHEMA_DROP_KEYS = {
    "title", "default", "maxItems", "minLength", "maxLength", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "pattern", "format",
}
# Models that accept the server-side refusal fallback ("default" form) on the Claude API.
_FALLBACK_MODEL_PREFIXES = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5")

M = TypeVar("M", bound=BaseModel)


class LLMError(Exception):
    """Any LLM failure. Callers treat it like a source failure (abstain), never as a verdict."""


class LLMTimeout(LLMError):
    pass


class LLMInvalidOutput(LLMError):
    pass


class LLMRefusal(LLMError):
    pass


class LLMUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: LLMUsage) -> LLMUsage:
        return LLMUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class _Transient(Exception):
    """Retryable provider failure (timeout, connection error, 429, 5xx)."""


# --------------------------------------------------------------------------- prompts


@lru_cache(maxsize=32)
def load_prompt(name: str) -> tuple[str, str]:
    """Return (system, user) templates from `prompts/<name>.txt`."""
    raw = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
    m = re.search(r"^USER:\s*$", raw, flags=re.MULTILINE)
    if not m:
        raise ValueError(f"prompt {name!r} has no 'USER:' section")
    system = re.sub(r"^\s*SYSTEM:\s*\n", "", raw[: m.start()]).strip()
    user = raw[m.end():].strip()
    return system, user


def render(template: str, variables: dict[str, str]) -> str:
    """Single-pass `{name}` substitution; unknown placeholders (e.g. JSON braces) are left as is."""
    return _PLACEHOLDER.sub(lambda m: str(variables[m.group(1)]) if m.group(1) in variables else m.group(0), template)


def json_schema_for(schema: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema reduced to what structured outputs accept; pydantic re-validates afterwards."""

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: clean(v) for k, v in node.items() if k not in _SCHEMA_DROP_KEYS}
            if out.get("minItems", 0) > 1:
                out.pop("minItems")
            if out.get("type") == "object":
                out["additionalProperties"] = False
            return out
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    return clean(schema.model_json_schema())


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text)


# --------------------------------------------------------------------------- client


class LLMClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        anthropic_http_client: anthropic.DefaultAsyncHttpxClient | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._anthropic_http_client = anthropic_http_client
        self._http_client = http_client
        self._anthropic: anthropic.AsyncAnthropic | None = None

    # -- provider calls: each returns (text, usage) or raises _Transient / LLMError

    def _anthropic_client(self) -> anthropic.AsyncAnthropic:
        if self._anthropic is None:
            kw: dict[str, Any] = {
                "api_key": self.settings.llm_api_key,
                "timeout": self.settings.llm_timeout_s,
                "max_retries": 0,  # retries are ours (one, with backoff)
            }
            if self._anthropic_http_client is not None:
                kw["http_client"] = self._anthropic_http_client
            self._anthropic = anthropic.AsyncAnthropic(**kw)
        return self._anthropic

    async def _call_anthropic(self, system: str, user: str, schema_json: dict[str, Any], model: str) -> tuple[str, LLMUsage]:
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema_json}}
        if self.settings.llm_effort:
            output_config["effort"] = self.settings.llm_effort
        kw: dict[str, Any] = {}
        if self.settings.llm_anthropic_fallbacks and model.startswith(_FALLBACK_MODEL_PREFIXES):
            kw["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
            kw["extra_body"] = {"fallbacks": "default"}
        try:
            resp = await self._anthropic_client().messages.create(
                model=model,
                max_tokens=self.settings.llm_max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
                **kw,
            )
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
            raise _Transient(type(e).__name__) from e
        except anthropic.RateLimitError as e:
            raise _Transient("rate_limited") from e
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise _Transient(f"http_{e.status_code}") from e
            raise LLMError(f"anthropic http {e.status_code}") from e
        usage = LLMUsage(input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens)
        if resp.stop_reason == "refusal":
            raise LLMRefusal("model refused")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        if resp.stop_reason == "max_tokens":
            log.warning("llm_max_tokens", extra={"model": model})
        return text, usage

    async def _call_openai_compatible(
        self, system: str, user: str, schema_json: dict[str, Any], model: str
    ) -> tuple[str, LLMUsage]:
        s = self.settings
        system_full = (
            f"{system}\n\nReturn a single JSON object that validates against this JSON schema:\n"
            f"{json.dumps(schema_json, ensure_ascii=False)}"
        )
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": s.llm_max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system_full}, {"role": "user", "content": user}],
        }
        client = self._http_client or httpx.AsyncClient()
        try:
            r = await client.post(
                s.llm_base_url.rstrip("/") + "/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {s.llm_api_key}"},
                timeout=s.llm_timeout_s,
            )
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise _Transient(type(e).__name__) from e
        finally:
            if self._http_client is None:
                await client.aclose()
        if r.status_code == 429 or r.status_code >= 500:
            raise _Transient(f"http_{r.status_code}")
        if r.status_code >= 400:
            raise LLMError(f"openai_compatible http {r.status_code}")
        body = r.json()
        u = body.get("usage") or {}
        usage = LLMUsage(input_tokens=u.get("prompt_tokens", 0), output_tokens=u.get("completion_tokens", 0))
        return body["choices"][0]["message"]["content"] or "", usage

    async def _call_once(self, system: str, user: str, schema_json: dict[str, Any], model: str) -> tuple[str, LLMUsage]:
        provider = self.settings.llm_provider
        if provider == "anthropic":
            call = self._call_anthropic
        elif provider == "openai_compatible":
            call = self._call_openai_compatible
        else:
            raise LLMError("LLM_PROVIDER is not set")
        try:
            return await with_retry(lambda: call(system, user, schema_json, model), retry_on=(_Transient,))
        except _Transient as e:
            raise LLMTimeout(str(e)) from e

    # -- public

    async def complete_json(
        self, prompt_name: str, variables: dict[str, str], schema: type[M], model: str
    ) -> tuple[M, LLMUsage]:
        system_t, user_t = load_prompt(prompt_name)
        system, user = render(system_t, variables), render(user_t, variables)
        schema_json = json_schema_for(schema)
        usage = LLMUsage()
        t0 = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(2):  # one retry on invalid JSON
            text, u = await self._call_once(system, user, schema_json, model)
            usage = usage + u
            try:
                parsed = schema.model_validate(_extract_json(text))
                log.info(
                    "llm_ok",
                    extra={
                        "prompt": prompt_name,
                        "model": model,
                        "attempt": attempt + 1,
                        "latency_ms": int((time.perf_counter() - t0) * 1000),
                        "tokens_in": usage.input_tokens,
                        "tokens_out": usage.output_tokens,
                    },
                )
                return parsed, usage
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = e
                log.warning("llm_invalid_json", extra={"prompt": prompt_name, "model": model, "attempt": attempt + 1})
        raise LLMInvalidOutput(f"{prompt_name}: invalid JSON after retry") from last_error


@lru_cache
def get_llm_client() -> LLMClient:
    return LLMClient()


async def complete_json(
    prompt_name: str, variables: dict[str, str], schema: type[M], model: str
) -> tuple[M, LLMUsage]:
    return await get_llm_client().complete_json(prompt_name, variables, schema, model)
