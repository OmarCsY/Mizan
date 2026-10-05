"""Provider-agnostic multilingual embeddings, fixed at `EMBEDDING_DIM` (1024) dims (SPEC §14, TASKS B03).

Providers (chosen by `EMBEDDING_PROVIDER`):
- `gemini` (DECISIONS D-16): native `models/{model}:batchEmbedContents` (key in the `x-goog-api-key`
  header), `outputDimensionality=EMBEDDING_DIM`, `taskType` RETRIEVAL_DOCUMENT for the corpus and
  RETRIEVAL_QUERY for user quotes. Vectors are L2-normalized (Gemini returns them unnormalized for any
  dimension other than 3072).
- `openai_compatible`: `POST {base}/embeddings` with `dimensions`.
- `cohere`: `POST {base or https://api.cohere.com}/v2/embed` with `input_type`, `embedding_types=["float"]`,
  `output_dimension`.
- `voyage`: `POST {base or https://api.voyageai.com}/v1/embeddings` with `input_type`, `output_dimension`.

Texts are sent in batches of `EMBEDDING_BATCH_SIZE` (100). Free-tier limits are respected client-side with
`EMBEDDING_RPM` (counted per text) and `EMBEDDING_TPM` (estimated tokens). Every vector's length is checked
against `EMBEDDING_DIM`. A provider rate limit raises `EmbeddingRateLimited` (with the provider's retry
delay and whether it is a daily quota) so batch scripts can wait or stop and resume later.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

import httpx

from app.core.config import Settings, get_settings
from app.core.ratelimit import WindowLimiter
from app.core.retry import with_retry

InputType = Literal["document", "query"]

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_GEMINI_TASK = {"document": "RETRIEVAL_DOCUMENT", "query": "RETRIEVAL_QUERY"}


class EmbeddingError(Exception):
    pass


class EmbeddingRateLimited(EmbeddingError):
    def __init__(self, retry_after_s: float, daily: bool, detail: str = "") -> None:
        super().__init__(f"rate limited (retry after {retry_after_s:.0f} s, daily={daily}) {detail}".strip())
        self.retry_after_s = retry_after_s
        self.daily = daily


class _Transient(Exception):
    pass


@dataclass
class EmbedResult:
    vectors: list[list[float]]
    tokens: int  # reported by the provider, or estimated (chars / 4) when it reports none


def estimate_tokens(texts: list[str]) -> int:
    return sum(len(t) // 4 + 1 for t in texts)


def l2_normalize(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v))
    if norm == 0:
        raise EmbeddingError("zero vector")
    return [x / norm for x in v]


def _retry_after(body_text: str, headers: httpx.Headers) -> float:
    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body_text)
    if m:
        return float(m.group(1))
    try:
        return float(headers.get("retry-after", "60"))
    except ValueError:
        return 60.0


class EmbeddingClient:
    def __init__(self, settings: Settings | None = None, *, http_client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings or get_settings()
        self._http_client = http_client
        self._rpm = WindowLimiter(self.settings.embedding_rpm)
        self._tpm = WindowLimiter(self.settings.embedding_tpm)

    def _request(self, texts: list[str], input_type: InputType) -> tuple[str, dict[str, Any], dict[str, str]]:
        s = self.settings
        bearer = {"Authorization": f"Bearer {s.embedding_api_key}"}
        if s.embedding_provider == "gemini":
            model = s.embedding_model.removeprefix("models/")
            url = f"{(s.embedding_base_url or GEMINI_BASE).rstrip('/')}/models/{model}:batchEmbedContents"
            payload = {
                "requests": [
                    {
                        "model": f"models/{model}",
                        "content": {"parts": [{"text": t}]},
                        "taskType": _GEMINI_TASK[input_type],
                        "outputDimensionality": s.embedding_dim,
                    }
                    for t in texts
                ]
            }
            return url, payload, {"x-goog-api-key": s.embedding_api_key}
        if s.embedding_provider == "openai_compatible":
            url = s.embedding_base_url.rstrip("/") + "/embeddings"
            return url, {"model": s.embedding_model, "input": texts, "dimensions": s.embedding_dim}, bearer
        if s.embedding_provider == "cohere":
            url = (s.embedding_base_url or "https://api.cohere.com").rstrip("/") + "/v2/embed"
            return url, {
                "model": s.embedding_model,
                "texts": texts,
                "input_type": "search_document" if input_type == "document" else "search_query",
                "embedding_types": ["float"],
                "output_dimension": s.embedding_dim,
            }, bearer
        if s.embedding_provider == "voyage":
            url = (s.embedding_base_url or "https://api.voyageai.com").rstrip("/") + "/v1/embeddings"
            return url, {
                "model": s.embedding_model,
                "input": texts,
                "input_type": input_type,
                "output_dimension": s.embedding_dim,
            }, bearer
        raise EmbeddingError("EMBEDDING_PROVIDER is not set")

    def _parse(self, body: dict[str, Any], texts: list[str]) -> tuple[list[list[float]], int]:
        p = self.settings.embedding_provider
        if p == "gemini":
            vectors = [l2_normalize(e["values"]) for e in body["embeddings"]]
            return vectors, estimate_tokens(texts)
        if p == "cohere":
            vectors = body["embeddings"]["float"]
            tokens = int((body.get("meta") or {}).get("billed_units", {}).get("input_tokens", 0))
            return vectors, tokens
        data = sorted(body["data"], key=lambda d: d.get("index", 0))
        usage = body.get("usage") or {}
        return [d["embedding"] for d in data], int(usage.get("total_tokens", usage.get("prompt_tokens", 0)))

    async def _embed_batch(self, client: httpx.AsyncClient, texts: list[str], input_type: InputType) -> EmbedResult:
        url, payload, headers = self._request(texts, input_type)
        await self._rpm.acquire(len(texts))
        await self._tpm.acquire(estimate_tokens(texts))

        async def call() -> httpx.Response:
            try:
                r = await client.post(url, json=payload, headers=headers, timeout=self.settings.external_timeout_s)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                raise _Transient(type(e).__name__) from e
            if r.status_code >= 500:
                raise _Transient(f"http_{r.status_code}")
            return r

        try:
            r = await with_retry(call, retry_on=(_Transient,))
        except _Transient as e:
            raise EmbeddingError(f"embedding provider unavailable: {e}") from e
        if r.status_code == 429:
            raise EmbeddingRateLimited(_retry_after(r.text, r.headers), daily="PerDay" in r.text)
        if r.status_code == 402:
            raise EmbeddingError("embedding provider: billing / prepaid credits exhausted (HTTP 402)")
        if r.status_code >= 400:
            raise EmbeddingError(f"embedding http {r.status_code}")
        vectors, tokens = self._parse(r.json(), texts)
        if len(vectors) != len(texts):
            raise EmbeddingError(f"expected {len(texts)} vectors, got {len(vectors)}")
        dim = self.settings.embedding_dim
        for v in vectors:
            if len(v) != dim:
                raise EmbeddingError(f"expected {dim} dims, got {len(v)}")
        return EmbedResult(vectors, tokens)

    async def embed_with_usage(self, texts: list[str], input_type: InputType = "document") -> EmbedResult:
        if not texts:
            return EmbedResult([], 0)
        size = self.settings.embedding_batch_size
        client = self._http_client or httpx.AsyncClient()
        out = EmbedResult([], 0)
        try:
            for i in range(0, len(texts), size):
                res = await self._embed_batch(client, texts[i : i + size], input_type)
                out.vectors.extend(res.vectors)
                out.tokens += res.tokens
        finally:
            if self._http_client is None:
                await client.aclose()
        return out

    async def embed(self, texts: list[str], input_type: InputType = "document") -> list[list[float]]:
        return (await self.embed_with_usage(texts, input_type)).vectors


@lru_cache
def get_embedding_client() -> EmbeddingClient:
    return EmbeddingClient()


async def embed(texts: list[str], input_type: InputType = "document") -> list[list[float]]:
    return await get_embedding_client().embed(texts, input_type)
