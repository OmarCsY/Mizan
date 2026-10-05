"""Provider-agnostic multilingual embeddings, fixed at `EMBEDDING_DIM` (1024) dims (SPEC §14, TASKS B03).

Providers (chosen by `EMBEDDING_PROVIDER`):
- `openai_compatible`: `POST {base}/embeddings` with `dimensions`.
- `cohere`: `POST {base or https://api.cohere.com}/v2/embed` with `input_type`, `embedding_types=["float"]`,
  `output_dimension`.
- `voyage`: `POST {base or https://api.voyageai.com}/v1/embeddings` with `input_type`, `output_dimension`.

Texts are sent in batches of `EMBEDDING_BATCH_SIZE` (100). Every vector's length is checked against
`EMBEDDING_DIM`; a mismatch raises `EmbeddingError` rather than storing a wrong-sized vector.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

import httpx

from app.core.config import Settings, get_settings
from app.core.retry import with_retry

InputType = Literal["document", "query"]


class EmbeddingError(Exception):
    pass


class _Transient(Exception):
    pass


@dataclass
class EmbedResult:
    vectors: list[list[float]]
    tokens: int


class EmbeddingClient:
    def __init__(self, settings: Settings | None = None, *, http_client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings or get_settings()
        self._http_client = http_client

    def _request(self, texts: list[str], input_type: InputType) -> tuple[str, dict[str, Any]]:
        s = self.settings
        if s.embedding_provider == "openai_compatible":
            url = s.embedding_base_url.rstrip("/") + "/embeddings"
            return url, {"model": s.embedding_model, "input": texts, "dimensions": s.embedding_dim}
        if s.embedding_provider == "cohere":
            url = (s.embedding_base_url or "https://api.cohere.com").rstrip("/") + "/v2/embed"
            return url, {
                "model": s.embedding_model,
                "texts": texts,
                "input_type": "search_document" if input_type == "document" else "search_query",
                "embedding_types": ["float"],
                "output_dimension": s.embedding_dim,
            }
        if s.embedding_provider == "voyage":
            url = (s.embedding_base_url or "https://api.voyageai.com").rstrip("/") + "/v1/embeddings"
            return url, {
                "model": s.embedding_model,
                "input": texts,
                "input_type": input_type,
                "output_dimension": s.embedding_dim,
            }
        raise EmbeddingError("EMBEDDING_PROVIDER is not set")

    def _parse(self, body: dict[str, Any]) -> tuple[list[list[float]], int]:
        p = self.settings.embedding_provider
        if p == "cohere":
            vectors = body["embeddings"]["float"]
            tokens = int((body.get("meta") or {}).get("billed_units", {}).get("input_tokens", 0))
        else:
            data = sorted(body["data"], key=lambda d: d.get("index", 0))
            vectors = [d["embedding"] for d in data]
            usage = body.get("usage") or {}
            tokens = int(usage.get("total_tokens", usage.get("prompt_tokens", 0)))
        return vectors, tokens

    async def _embed_batch(self, client: httpx.AsyncClient, texts: list[str], input_type: InputType) -> EmbedResult:
        url, payload = self._request(texts, input_type)

        async def call() -> httpx.Response:
            try:
                r = await client.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.settings.embedding_api_key}"},
                    timeout=self.settings.external_timeout_s,
                )
            except (httpx.TimeoutException, httpx.TransportError) as e:
                raise _Transient(type(e).__name__) from e
            if r.status_code == 429 or r.status_code >= 500:
                raise _Transient(f"http_{r.status_code}")
            return r

        try:
            r = await with_retry(call, retry_on=(_Transient,))
        except _Transient as e:
            raise EmbeddingError(f"embedding provider unavailable: {e}") from e
        if r.status_code >= 400:
            raise EmbeddingError(f"embedding http {r.status_code}")
        vectors, tokens = self._parse(r.json())
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
