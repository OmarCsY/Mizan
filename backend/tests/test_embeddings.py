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
