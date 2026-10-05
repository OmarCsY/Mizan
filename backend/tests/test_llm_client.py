from __future__ import annotations

import json
from pathlib import Path

import anthropic
import httpx
import httpx2
import pytest
import respx
from pydantic import BaseModel

from app.core.config import Settings
from app.llm import client as llm_mod
from app.llm.client import LLMClient, LLMInvalidOutput, LLMTimeout, json_schema_for, render


class Echo(BaseModel):
    answer: str
    score: float


@pytest.fixture(autouse=True)
def prompt_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "echo.txt").write_text(
        'SYSTEM:\nYou return JSON like {"answer": "...", "score": 0.0}.\n\nUSER:\n<message>{text}</message>\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(llm_mod, "PROMPTS_DIR", tmp_path)
    llm_mod.load_prompt.cache_clear()


def settings(provider: str) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider=provider,
        llm_api_key="test-key",
        llm_base_url="https://llm.test/v1",
        llm_timeout_s=1.0,
    )


# ----------------------------------------------------------------------------- anthropic


def anthropic_body(text: str, tin: int = 10, tout: int = 5) -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "m",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": tin, "output_tokens": tout},
    }


def anthropic_client(responses: list) -> tuple[LLMClient, list[dict]]:
    seen: list[dict] = []
    queue = list(responses)

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(req.content))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return httpx2.Response(200, json=item)

    http = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    return LLMClient(settings("anthropic"), anthropic_http_client=http), seen


async def test_anthropic_valid_json() -> None:
    c, seen = anthropic_client([anthropic_body('{"answer": "ok", "score": 0.9}')])
    out, usage = await c.complete_json("echo", {"text": "hello"}, Echo, "claude-opus-5-5")
    assert out == Echo(answer="ok", score=0.9)
    assert (usage.input_tokens, usage.output_tokens) == (10, 5)
    req = seen[0]
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert req["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert "temperature" not in req  # current Claude models reject sampling params (D-12)
    assert req["messages"][0]["content"] == "<message>hello</message>"


async def test_anthropic_invalid_then_valid_retries_once() -> None:
    c, seen = anthropic_client([anthropic_body("not json"), anthropic_body('{"answer": "ok", "score": 1}')])
    out, usage = await c.complete_json("echo", {"text": "x"}, Echo, "claude-opus-5-5")
    assert out.answer == "ok"
    assert len(seen) == 2
    assert usage.input_tokens == 20  # usage summed over both attempts


async def test_anthropic_invalid_twice_raises() -> None:
    c, _ = anthropic_client([anthropic_body('{"answer": 1}'), anthropic_body("{}")])
    with pytest.raises(LLMInvalidOutput):
        await c.complete_json("echo", {"text": "x"}, Echo, "claude-opus-5-5")


async def test_anthropic_timeout_retries_then_raises() -> None:
    c, seen = anthropic_client([httpx2.ReadTimeout("t"), httpx2.ReadTimeout("t")])
    with pytest.raises(LLMTimeout):
        await c.complete_json("echo", {"text": "x"}, Echo, "claude-opus-5-5")
    assert len(seen) == 2  # one retry


# ----------------------------------------------------------------------------- openai_compatible


def oa_body(text: str) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }


@respx.mock
async def test_openai_compatible_valid_json() -> None:
    route = respx.post("https://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=oa_body('{"answer": "ok", "score": 0.5}'))
    )
    out, usage = await LLMClient(settings("openai_compatible")).complete_json("echo", {"text": "hi"}, Echo, "m")
    assert out.score == 0.5 and usage.input_tokens == 7
    body = json.loads(route.calls[0].request.content)
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    assert route.calls[0].request.headers["Authorization"] == "Bearer test-key"


@respx.mock
async def test_openai_compatible_invalid_then_valid() -> None:
    route = respx.post("https://llm.test/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=oa_body("```json\n{oops")),
            httpx.Response(200, json=oa_body('```json\n{"answer": "a", "score": 0}\n```')),
        ]
    )
    out, _ = await LLMClient(settings("openai_compatible")).complete_json("echo", {"text": "hi"}, Echo, "m")
    assert out.answer == "a" and route.call_count == 2


@respx.mock
async def test_openai_compatible_timeout() -> None:
    route = respx.post("https://llm.test/v1/chat/completions").mock(side_effect=httpx.ReadTimeout("t"))
    with pytest.raises(LLMTimeout):
        await LLMClient(settings("openai_compatible")).complete_json("echo", {"text": "hi"}, Echo, "m")
    assert route.call_count == 2


# ----------------------------------------------------------------------------- misc


def test_provider_switch_is_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    assert Settings(_env_file=None).llm_provider == "openai_compatible"
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert Settings(_env_file=None).llm_provider == "anthropic"


def test_render_is_single_pass() -> None:
    # user text that looks like a placeholder must not be substituted again
    assert render("<m>{text}</m> {other}", {"text": "{other}"}) == "<m>{other}</m> {other}"


def test_schema_cleaning_drops_unsupported_constraints() -> None:
    from pydantic import Field

    class S(BaseModel):
        xs: list[str] = Field(min_length=1, max_length=3)

    js = json_schema_for(S)
    assert "maxItems" not in js["properties"]["xs"] and js["additionalProperties"] is False


# ----------------------------------------------------------------------------- quota fallback (D-15)

GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai"
GROQ = "https://api.groq.com/openai/v1"


def gemini_settings(groq_key: str = "groq-key") -> Settings:
    return Settings(
        _env_file=None, llm_provider="openai_compatible", llm_api_key="g", llm_base_url=GEMINI + "/",
        groq_api_key=groq_key, llm_timeout_s=1.0,
    )


@pytest.mark.parametrize("status", [429, 402])
@respx.mock
async def test_quota_error_falls_back_to_groq_once(status: int) -> None:
    primary = respx.post(GEMINI + "/chat/completions").mock(
        return_value=httpx.Response(status, json={"error": {"status": "RESOURCE_EXHAUSTED"}})
    )
    groq = respx.post(GROQ + "/chat/completions").mock(
        return_value=httpx.Response(200, json=oa_body('{"answer": "g", "score": 1}'))
    )
    out, usage = await LLMClient(gemini_settings()).complete_json("echo", {"text": "hi"}, Echo, "gemini-x")
    assert out.answer == "g"
    assert primary.call_count == 1  # quota errors are not retried on the same provider
    assert groq.call_count == 1
    body = json.loads(groq.calls[0].request.content)
    assert body["model"] == "openai/gpt-oss-120b" and body["reasoning_effort"] == "low"
    assert groq.calls[0].request.headers["Authorization"] == "Bearer groq-key"
    assert usage.providers == {"groq": 1}


@respx.mock
async def test_quota_without_groq_key_raises() -> None:
    from app.llm.client import LLMQuotaExceeded

    respx.post(GEMINI + "/chat/completions").mock(return_value=httpx.Response(429, json={}))
    with pytest.raises(LLMQuotaExceeded):
        await LLMClient(gemini_settings(groq_key="")).complete_json("echo", {"text": "hi"}, Echo, "gemini-x")


@respx.mock
async def test_groq_also_out_of_quota_raises() -> None:
    from app.llm.client import LLMQuotaExceeded

    respx.post(GEMINI + "/chat/completions").mock(return_value=httpx.Response(429, json={}))
    groq = respx.post(GROQ + "/chat/completions").mock(return_value=httpx.Response(429, json={}))
    with pytest.raises(LLMQuotaExceeded):
        await LLMClient(gemini_settings()).complete_json("echo", {"text": "hi"}, Echo, "gemini-x")
    assert groq.call_count == 1


@respx.mock
async def test_primary_success_is_labelled_gemini() -> None:
    respx.post(GEMINI + "/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=oa_body("bad")),
            httpx.Response(200, json=oa_body('{"answer": "a", "score": 0}')),
        ]
    )
    _, usage = await LLMClient(gemini_settings()).complete_json("echo", {"text": "hi"}, Echo, "gemini-x")
    assert usage.providers == {"gemini": 2}  # both attempts counted
