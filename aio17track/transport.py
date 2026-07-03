"""Internal HTTP transport (SPEC §4). Not part of the public API.

Owns all HTTP concerns: ``17token`` header injection, JSON content type,
a 3 req/s token-bucket throttle applied before every request, retry with
exponential backoff + jitter on 429/5xx (honoring ``Retry-After``), and
envelope unwrap. The transport never inspects per-item rejections; that
belongs to the model layer.
"""

from typing import Any

import aiohttp


class _Transport:
    """Authenticated, throttled, retrying POST-only JSON transport."""

    def __init__(
        self,
        api_key: str,
        *,
        session: aiohttp.ClientSession | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        raise NotImplementedError

    async def request(self, endpoint: str, payload: object) -> dict[str, Any]:
        """POST ``payload``, unwrap the ``{code, data}`` envelope.

        Returns ``data`` when ``code == 0``; raises the mapped exception
        (SPEC §7) when ``code != 0`` at the request level.

        TODO(M2): ``gettracklist`` responses carry a top-level ``page``
        object as a *sibling* of ``data``; the list path must preserve the
        full envelope (``TrackListPage.from_api`` requires it) instead of
        going through this data-only unwrap.
        """
        raise NotImplementedError

    async def close(self) -> None:
        """Close the session only if this transport created it."""
        raise NotImplementedError
