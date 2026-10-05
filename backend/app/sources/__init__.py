"""External source clients. Every call has a timeout and one retry; failure raises SourceUnavailable."""

from __future__ import annotations

import httpx

from app.core.retry import with_retry


class SourceUnavailable(Exception):
    """The source did not answer (timeout, connection error, 429/5xx, unparseable body).

    An outage is not a result: callers map this to `source_status = "source_unavailable"`, never `not_found`.
    """

    def __init__(self, source: str, reason: str) -> None:
        super().__init__(f"{source}: {reason}")
        self.source = source
        self.reason = reason


class _Transient(Exception):
    pass


USER_AGENT = "Mizan/0.1 (Islamic quotation verifier; hackathon project)"


async def get_json_with_retry(
    client: httpx.AsyncClient, source: str, url: str, *, params: dict | None = None, timeout_s: float = 8.0
) -> object:
    """GET a JSON body with one retry (backoff) on timeouts, transport errors, 429 and 5xx."""

    async def call() -> httpx.Response:
        try:
            r = await client.get(url, params=params, timeout=timeout_s)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise _Transient(type(e).__name__) from e
        if r.status_code == 429 or r.status_code >= 500:
            raise _Transient(f"http_{r.status_code}")
        return r

    try:
        r = await with_retry(call, retry_on=(_Transient,))
    except _Transient as e:
        raise SourceUnavailable(source, str(e)) from e
    if r.status_code >= 400:
        raise SourceUnavailable(source, f"http_{r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise SourceUnavailable(source, "invalid_json") from e
