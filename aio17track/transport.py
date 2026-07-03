"""Internal HTTP transport (SPEC §4). Not part of the public API.

Owns all HTTP concerns: ``17token`` header injection, JSON content type,
a 3 req/s token-bucket throttle applied before every request, retry with
exponential backoff + jitter on 429/5xx (honoring ``Retry-After``), and
envelope unwrap with the SPEC §7 error mapping. The transport never
inspects per-item rejections; that belongs to the model layer.

The clock, sleep, and RNG are injectable so throttle and retry behavior
is testable with fake time.
"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, NoReturn

import aiohttp

from ._constants import EPSILON, MIN_SLEEP_SECONDS
from .errors import (
    AuthenticationError,
    QuotaExhaustedError,
    RateLimitError,
    Track17APIError,
    Track17ConnectionError,
)

_BASE_URL = "https://api.17track.net/track/v2.4"
_AUTH_HEADER = "17token"
_REQUESTS_PER_SECOND = 3.0

# SPEC §7 request-level mapping.
_AUTH_ERROR_CODES = frozenset({-18010001, -18010002, -18010004})
_QUOTA_ERROR_CODES = frozenset({-18019907, -18019908})

type _Clock = Callable[[], float]
type _Sleep = Callable[[float], Awaitable[None]]


class _TokenBucket:
    """Token-bucket limiter: ``acquire()`` blocks until a token is available."""

    def __init__(
        self,
        rate: float,
        capacity: float,
        *,
        clock: _Clock = time.monotonic,
        sleep: _Sleep = asyncio.sleep,
    ) -> None:
        self._rate = rate
        self._capacity = capacity
        self._clock = clock
        self._sleep = sleep
        self._tokens = capacity
        self._updated = clock()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._updated) * self._rate
                )
                self._updated = now
                if self._tokens >= 1.0 - EPSILON:
                    self._tokens = max(0.0, self._tokens - 1.0)
                    return
                await self._sleep(max((1.0 - self._tokens) / self._rate, MIN_SLEEP_SECONDS))


def _parse_retry_after(header: str | None) -> float | None:
    """Parse a Retry-After header (delta-seconds form only)."""
    if header is None:
        return None
    try:
        value = float(header)
    except ValueError:
        return None
    return value if value >= 0 else None


def _retry_delay(attempt: int, retry_after: float | None, rng: random.Random) -> float:
    """Exponential backoff with jitter; an explicit Retry-After wins."""
    if retry_after is not None:
        return retry_after
    base = 0.5 * (2.0 ** (attempt - 1))
    return base + rng.uniform(0.0, base / 4.0)


def _raise_mapped(code: int, message: str) -> NoReturn:
    """Raise the SPEC §7 exception for a request-level error code."""
    if code in _AUTH_ERROR_CODES:
        raise AuthenticationError(f"[{code}] {message}")
    if code in _QUOTA_ERROR_CODES:
        raise QuotaExhaustedError(f"[{code}] {message}")
    raise Track17APIError(code, message)


def _check_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Validate the outer ``{code, data}`` envelope, raising on request-level errors.

    Request-level failures surface two ways on the wire: a non-zero outer
    ``code``, or ``data.errors[]`` alongside ``code == 0`` (the documented
    illegal-parameter shape). Both raise; per-item ``rejected`` entries are
    left for the model layer.
    """
    code = envelope.get("code")
    if isinstance(code, int) and code != 0:
        _raise_mapped(code, f"request-level error {code}")
    data = envelope.get("data")
    if isinstance(data, dict):
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0] if isinstance(errors[0], dict) else {}
            first_code = first.get("code")
            _raise_mapped(
                first_code if isinstance(first_code, int) else -1,
                str(first.get("message") or "request rejected"),
            )
    return envelope


class _Transport:
    """Authenticated, throttled, retrying POST-only JSON transport."""

    def __init__(
        self,
        api_key: str,
        *,
        session: aiohttp.ClientSession | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        clock: _Clock = time.monotonic,
        sleep: _Sleep = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._api_key = api_key
        self._external_session = session
        self._owned_session: aiohttp.ClientSession | None = None
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._max_retries = max_retries
        self._sleep = sleep
        self._rng = rng if rng is not None else random.Random()
        self._bucket = _TokenBucket(
            _REQUESTS_PER_SECOND, _REQUESTS_PER_SECOND, clock=clock, sleep=sleep
        )

    def _session(self) -> aiohttp.ClientSession:
        """Return the external session, or lazily create (and own) one."""
        if self._external_session is not None:
            return self._external_session
        if self._owned_session is None or self._owned_session.closed:
            self._owned_session = aiohttp.ClientSession()
        return self._owned_session

    async def request(self, endpoint: str, payload: object) -> dict[str, Any]:
        """POST ``payload``, unwrap the envelope, and return ``data``.

        Raises the mapped exception (SPEC §7) on request-level errors. Use
        :meth:`request_envelope` for list endpoints — ``gettracklist``
        carries a top-level ``page`` sibling of ``data`` that this unwrap
        would discard (``TrackListPage.from_api`` requires the envelope).
        """
        envelope = await self.request_envelope(endpoint, payload)
        data = envelope.get("data")
        return data if isinstance(data, dict) else {}

    async def request_envelope(self, endpoint: str, payload: object) -> dict[str, Any]:
        """POST ``payload`` and return the full validated response envelope."""
        attempt = 1
        while True:
            await self._bucket.acquire()
            try:
                async with self._session().post(
                    f"{_BASE_URL}/{endpoint}",
                    json=payload,
                    headers={_AUTH_HEADER: self._api_key},
                    timeout=self._timeout,
                ) as response:
                    if response.status == 429 or response.status >= 500:
                        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                        if attempt >= self._max_retries:
                            if response.status == 429:
                                raise RateLimitError(
                                    "rate limited by 17track and retries exhausted",
                                    retry_after=retry_after,
                                )
                            raise Track17APIError(
                                response.status,
                                f"HTTP {response.status} from 17track after "
                                f"{attempt} attempts",
                            )
                        await self._sleep(_retry_delay(attempt, retry_after, self._rng))
                        attempt += 1
                        continue
                    if response.status == 401:
                        raise AuthenticationError(
                            "HTTP 401: check the 17token API key, IP whitelist, "
                            "and account status"
                        )
                    if response.status >= 400:
                        # Non-retryable 4xx fail fast (SPEC §4).
                        raise Track17APIError(
                            response.status, f"HTTP {response.status} from 17track"
                        )
                    try:
                        envelope: dict[str, Any] = await response.json(content_type=None)
                    except ValueError as exc:  # malformed JSON body
                        raise Track17APIError(-1, "response body is not valid JSON") from exc
            except TimeoutError as exc:
                raise Track17ConnectionError(
                    f"request to '{endpoint}' timed out"
                ) from exc
            except aiohttp.ClientError as exc:
                raise Track17ConnectionError(str(exc)) from exc
            return _check_envelope(envelope)

    async def close(self) -> None:
        """Close the session only if this transport created it."""
        if self._owned_session is not None and not self._owned_session.closed:
            await self._owned_session.close()
