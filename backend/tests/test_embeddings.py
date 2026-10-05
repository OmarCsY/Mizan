from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.core.config import Settings
from app.llm.embeddings import EmbeddingClient, EmbeddingError

DIM = 1024


def settings(provider: str = "openai_compatible", **kw: object) -> Settings:
    return Settings(
        _env_file=None,
        embedding_provider=provider,
        embedding_api_key="k",
        embedding_base_url="https://emb.test/v1",
        embedding_model="m",
        embedding_dim=DIM,
        external_timeout_s=1.0,
        **kw,
    )


def oa_response(request: httpx.Request) -> httpx.Response:
    n = len(json.loads(request.content)["input"])
    return httpx.Response(
        200, json={"data": [{"index": i, "embedding": [0.0] * DIM} for i in range(n)], "usage": {"total_tokens": n}}
    )


@respx.mock
async def test_batches_of_100_and_dimension() -> None:
    route = respx.post("https://emb.test/v1/embeddings").mock(side_effect=oa_response)
    res = await EmbeddingClient(settings()).embed_with_usage([f"t{i}" for i in range(250)])
    assert route.call_count == 3
    assert [len(json.loads(c.request.content)["input"]) for c in route.calls] == [100, 100, 50]
    assert len(res.vectors) == 250 and all(len(v) == DIM for v in res.vectors)
    assert res.tokens == 250
    assert json.loads(route.calls[0].request.content)["dimensions"] == DIM


@respx.mock
async def test_wrong_dimension_raises() -> None:
    respx.post("https://emb.test/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0] * 768}]})
    )
    with pytest.raises(EmbeddingError):
        await EmbeddingClient(settings()).embed(["x"])


@respx.mock
async def test_timeout_retries_then_raises() -> None:
    route = respx.post("https://emb.test/v1/embeddings").mock(side_effect=httpx.ReadTimeout("t"))
    with pytest.raises(EmbeddingError):
        await EmbeddingClient(settings()).embed(["x"])
    assert route.call_count == 2


@respx.mock
async def test_cohere_shape() -> None:
    route = respx.post("https://emb.test/v1/v2/embed").mock(
        return_value=httpx.Response(200, json={"embeddings": {"float": [[0.1] * DIM]}, "meta": {"billed_units": {"input_tokens": 4}}})
    )
    res = await EmbeddingClient(settings("cohere")).embed_with_usage(["x"], input_type="query")
    body = json.loads(route.calls[0].request.content)
    assert body["input_type"] == "search_query" and body["output_dimension"] == DIM
    assert res.tokens == 4 and len(res.vectors[0]) == DIM


async def test_empty_input_makes_no_call() -> None:
    assert await EmbeddingClient(settings()).embed([]) == []


# ----------------------------------------------------------------------------- gemini (D-16)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents"


def gemini_settings(**kw: object) -> Settings:
    return Settings(
        _env_file=None, embedding_provider="gemini", embedding_api_key="gk", embedding_model="gemini-embedding-001",
        embedding_dim=DIM, external_timeout_s=1.0, **kw,
    )


def gemini_response(request: httpx.Request) -> httpx.Response:
    n = len(json.loads(request.content)["requests"])
    return httpx.Response(200, json={"embeddings": [{"values": [3.0, 4.0] + [0.0] * (DIM - 2)} for _ in range(n)]})


@respx.mock
async def test_gemini_request_shape_and_l2_normalization() -> None:
    route = respx.post(GEMINI_URL).mock(side_effect=gemini_response)
    res = await EmbeddingClient(gemini_settings()).embed_with_usage(["a", "b"], input_type="document")
    req = route.calls[0].request
    body = json.loads(req.content)
    assert req.headers["x-goog-api-key"] == "gk" and "key=" not in str(req.url)  # key never in the URL
    assert {r["taskType"] for r in body["requests"]} == {"RETRIEVAL_DOCUMENT"}
    assert {r["outputDimensionality"] for r in body["requests"]} == {DIM}
    assert body["requests"][0]["model"] == "models/gemini-embedding-001"
    v = res.vectors[0]
    assert abs(sum(x * x for x in v) - 1.0) < 1e-9 and abs(v[0] - 0.6) < 1e-9  # (3,4)/5
    assert res.tokens > 0  # estimated


@respx.mock
async def test_gemini_query_task_type() -> None:
    route = respx.post(GEMINI_URL).mock(side_effect=gemini_response)
    await EmbeddingClient(gemini_settings()).embed(["q"], input_type="query")
    assert json.loads(route.calls[0].request.content)["requests"][0]["taskType"] == "RETRIEVAL_QUERY"


@respx.mock
async def test_gemini_batches_of_100() -> None:
    route = respx.post(GEMINI_URL).mock(side_effect=gemini_response)
    await EmbeddingClient(gemini_settings()).embed([f"t{i}" for i in range(130)])
    assert [len(json.loads(c.request.content)["requests"]) for c in route.calls] == [100, 30]


@respx.mock
async def test_gemini_429_reports_retry_delay_and_daily_flag() -> None:
    from app.llm.embeddings import EmbeddingRateLimited

    body = '{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [{"violations": [{"quotaId": "EmbedContentRequestsPerDayPerProject"}]}, {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"}]}}'
    route = respx.post(GEMINI_URL).mock(return_value=httpx.Response(429, text=body))
    with pytest.raises(EmbeddingRateLimited) as ei:
        await EmbeddingClient(gemini_settings()).embed(["x"])
    assert ei.value.retry_after_s == 37 and ei.value.daily is True
    assert route.call_count == 1  # not retried blindly; the caller decides to wait or stop


@respx.mock
async def test_gemini_402_is_a_clear_error() -> None:
    respx.post(GEMINI_URL).mock(return_value=httpx.Response(402, json={"error": {"code": 402}}))
    with pytest.raises(EmbeddingError, match="402"):
        await EmbeddingClient(gemini_settings()).embed(["x"])


# ----------------------------------------------------------------------------- rate limiter


async def test_window_limiter_waits_for_capacity() -> None:
    from app.core.ratelimit import WindowLimiter

    now = [0.0]
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    lim = WindowLimiter(100, 60.0, clock=lambda: now[0], sleep=fake_sleep)
    assert await lim.acquire(60) == 0
    now[0] = 10.0
    assert await lim.acquire(40) == 0  # exactly at capacity
    waited = await lim.acquire(1)  # must wait until the first 60 units leave the window (t=60)
    assert 49.9 < waited < 50.1 and now[0] >= 60.0


async def test_window_limiter_disabled_when_zero() -> None:
    from app.core.ratelimit import WindowLimiter

    assert await WindowLimiter(0).acquire(10_000) == 0
