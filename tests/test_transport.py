"""_Transport: auth, throttle, retry, envelope unwrap (SPEC §4, §11).

Throttle and retry pacing are tested with a fake clock/sleep; HTTP is
mocked with aioresponses. No real time passes and no network is touched.
"""

import random

import aiohttp
import pytest
from aioresponses import aioresponses
from aioresponses.core import RequestCall
from yarl import URL

from aio17track import (
    AuthenticationError,
    QuotaExhaustedError,
    RateLimitError,
    Track17APIError,
    Track17ConnectionError,
    TrackListPage,
)
from aio17track.transport import (
    _check_envelope,
    _parse_retry_after,
    _retry_delay,
    _TokenBucket,
    _Transport,
)

_QUOTA_URL = "https://api.17track.net/track/v2.4/getquota"
_LIST_URL = "https://api.17track.net/track/v2.4/gettracklist"


def _calls(mocked: aioresponses, url: str) -> list[RequestCall]:
    return mocked.requests[("POST", URL(url))]


class FakeTime:
    """Manual clock; sleeping records the delay and advances the clock."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


def _transport(fake: FakeTime, **kwargs: object) -> _Transport:
    return _Transport(
        "test-key",
        clock=fake.clock,
        sleep=fake.sleep,
        rng=random.Random(17),
        **kwargs,  # type: ignore[arg-type]
    )


# --- token bucket ---


async def test_bucket_allows_a_burst_of_three_without_waiting() -> None:
    fake = FakeTime()
    bucket = _TokenBucket(3.0, 3.0, clock=fake.clock, sleep=fake.sleep)
    for _ in range(3):
        await bucket.acquire()
    assert fake.sleeps == []


async def test_bucket_paces_the_fourth_request() -> None:
    fake = FakeTime()
    bucket = _TokenBucket(3.0, 3.0, clock=fake.clock, sleep=fake.sleep)
    for _ in range(4):
        await bucket.acquire()
    assert len(fake.sleeps) == 1
    assert fake.sleeps[0] == pytest.approx(1.0 / 3.0)


async def test_bucket_refills_over_time() -> None:
    fake = FakeTime()
    bucket = _TokenBucket(3.0, 3.0, clock=fake.clock, sleep=fake.sleep)
    for _ in range(3):
        await bucket.acquire()
    fake.now += 1.0  # a full second refills all 3 tokens
    for _ in range(3):
        await bucket.acquire()
    assert fake.sleeps == []


async def test_bucket_sustains_three_per_second() -> None:
    fake = FakeTime()
    bucket = _TokenBucket(3.0, 3.0, clock=fake.clock, sleep=fake.sleep)
    for _ in range(12):
        await bucket.acquire()
    # 12 acquisitions from a burst capacity of 3 at 3/s: 9 must wait ~1/3 s each.
    assert len(fake.sleeps) == 9
    assert fake.now == pytest.approx(3.0)


# --- retry helpers ---


def test_parse_retry_after() -> None:
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("2.5") == 2.5
    assert _parse_retry_after("0") == 0.0
    assert _parse_retry_after("-3") is None
    assert _parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None  # date form unsupported


def test_retry_after_wins_over_backoff() -> None:
    assert _retry_delay(1, 7.0, random.Random(17)) == 7.0


def test_backoff_grows_exponentially_with_bounded_jitter() -> None:
    rng = random.Random(17)
    for attempt, base in ((1, 0.5), (2, 1.0), (3, 2.0)):
        delay = _retry_delay(attempt, None, rng)
        assert base <= delay <= base * 1.25


# --- envelope validation ---


def test_check_envelope_passes_through_clean_responses() -> None:
    envelope = {"code": 0, "data": {"accepted": [], "rejected": []}}
    assert _check_envelope(envelope) is envelope


@pytest.mark.parametrize("code", [-18010001, -18010002, -18010004])
def test_auth_codes_raise_authentication_error(code: int) -> None:
    with pytest.raises(AuthenticationError):
        _check_envelope({"code": code, "data": None})


@pytest.mark.parametrize("code", [-18019907, -18019908])
def test_quota_codes_raise_quota_exhausted(code: int) -> None:
    with pytest.raises(QuotaExhaustedError):
        _check_envelope({"code": code, "data": None})


def test_other_nonzero_codes_raise_api_error_with_code() -> None:
    with pytest.raises(Track17APIError) as excinfo:
        _check_envelope({"code": -18010003, "data": None})
    assert excinfo.value.code == -18010003


@pytest.mark.parametrize("envelope", [{}, {"data": {}}, {"code": "0", "data": {}}])
def test_missing_or_non_integer_code_is_not_success(envelope: dict[str, object]) -> None:
    """Only an integer code == 0 may pass; a gateway 200 without the 17track
    envelope must not turn into an empty success."""
    with pytest.raises(Track17APIError, match="code"):
        _check_envelope(envelope)


def test_data_errors_with_zero_code_still_raise() -> None:
    """The documented illegal-parameter shape: code 0 + data.errors[]."""
    envelope = {
        "code": 0,
        "data": {"errors": [{"code": -18010013, "message": "Submitted data is invalid."}]},
    }
    with pytest.raises(Track17APIError) as excinfo:
        _check_envelope(envelope)
    assert excinfo.value.code == -18010013
    assert excinfo.value.message == "Submitted data is invalid."


# --- transport over mocked HTTP ---


async def test_request_unwraps_data_and_sends_auth_header() -> None:
    fake = FakeTime()
    transport = _transport(fake)
    with aioresponses() as mocked:
        mocked.post(_QUOTA_URL, payload={"code": 0, "data": {"quota_remain": 42}})
        data = await transport.request("getquota", {})

        request_calls = _calls(mocked, _QUOTA_URL)
        assert request_calls[0].kwargs["headers"]["17token"] == "test-key"
    await transport.close()
    assert data == {"quota_remain": 42}


async def test_request_envelope_preserves_the_page_sibling() -> None:
    fake = FakeTime()
    transport = _transport(fake)
    envelope_payload = {
        "page": {"data_total": 43, "page_total": 2, "page_no": 1, "page_size": 40},
        "code": 0,
        "data": {"accepted": []},
    }
    with aioresponses() as mocked:
        mocked.post(_LIST_URL, payload=envelope_payload)
        envelope = await transport.request_envelope("gettracklist", {})
    await transport.close()

    page = TrackListPage.from_api(envelope)
    assert page.page_total == 2
    assert page.data_total == 43


async def test_http_401_raises_authentication_error_without_retry() -> None:
    fake = FakeTime()
    transport = _transport(fake)
    with aioresponses() as mocked:
        mocked.post(_QUOTA_URL, status=401)
        with pytest.raises(AuthenticationError):
            await transport.request("getquota", {})
        assert len(_calls(mocked, _QUOTA_URL)) == 1
    await transport.close()
    assert fake.sleeps == []


async def test_non_retryable_4xx_fails_fast() -> None:
    fake = FakeTime()
    transport = _transport(fake)
    with aioresponses() as mocked:
        mocked.post(_QUOTA_URL, status=404)
        with pytest.raises(Track17APIError) as excinfo:
            await transport.request("getquota", {})
        assert len(_calls(mocked, _QUOTA_URL)) == 1
    await transport.close()
    assert excinfo.value.code == 404
    assert fake.sleeps == []


async def test_429_retries_honor_retry_after_then_raise_rate_limit() -> None:
    fake = FakeTime()
    transport = _transport(fake)
    with aioresponses() as mocked:
        for _ in range(3):
            mocked.post(_QUOTA_URL, status=429, headers={"Retry-After": "2"})
        with pytest.raises(RateLimitError) as excinfo:
            await transport.request("getquota", {})
        assert len(_calls(mocked, _QUOTA_URL)) == 3  # capped attempts
    await transport.close()
    assert excinfo.value.retry_after == 2.0
    assert fake.sleeps.count(2.0) == 2  # two retry waits, straight from Retry-After


async def test_5xx_retries_then_succeeds() -> None:
    fake = FakeTime()
    transport = _transport(fake)
    with aioresponses() as mocked:
        mocked.post(_QUOTA_URL, status=500)
        mocked.post(_QUOTA_URL, payload={"code": 0, "data": {"quota_remain": 7}})
        data = await transport.request("getquota", {})
    await transport.close()
    assert data == {"quota_remain": 7}
    assert len(fake.sleeps) == 1  # one backoff between the two attempts


async def test_5xx_exhausting_retries_raises_api_error() -> None:
    fake = FakeTime()
    transport = _transport(fake)
    with aioresponses() as mocked:
        for _ in range(3):
            mocked.post(_QUOTA_URL, status=503)
        with pytest.raises(Track17APIError) as excinfo:
            await transport.request("getquota", {})
    await transport.close()
    assert excinfo.value.code == 503


async def test_max_retries_is_configurable() -> None:
    fake = FakeTime()
    transport = _transport(fake, max_retries=1)
    with aioresponses() as mocked:
        mocked.post(_QUOTA_URL, status=429)
        with pytest.raises(RateLimitError):
            await transport.request("getquota", {})
        assert len(_calls(mocked, _QUOTA_URL)) == 1
    await transport.close()


async def test_client_error_raises_connection_error() -> None:
    fake = FakeTime()
    transport = _transport(fake)
    with aioresponses() as mocked:
        mocked.post(_QUOTA_URL, exception=aiohttp.ClientConnectionError("boom"))
        with pytest.raises(Track17ConnectionError):
            await transport.request("getquota", {})
    await transport.close()


async def test_timeout_raises_connection_error() -> None:
    fake = FakeTime()
    transport = _transport(fake)
    with aioresponses() as mocked:
        mocked.post(_QUOTA_URL, exception=TimeoutError())
        with pytest.raises(Track17ConnectionError):
            await transport.request("getquota", {})
    await transport.close()


async def test_invalid_json_body_raises_api_error() -> None:
    fake = FakeTime()
    transport = _transport(fake)
    with aioresponses() as mocked:
        mocked.post(_QUOTA_URL, body="<html>gateway</html>", content_type="text/html")
        with pytest.raises(Track17APIError, match="JSON"):
            await transport.request("getquota", {})
    await transport.close()


async def test_envelope_error_codes_map_through_the_transport() -> None:
    fake = FakeTime()
    transport = _transport(fake)
    with aioresponses() as mocked:
        mocked.post(_QUOTA_URL, payload={"code": -18010002, "data": None})
        with pytest.raises(AuthenticationError):
            await transport.request("getquota", {})
    await transport.close()


# --- session ownership ---


async def test_close_closes_only_the_owned_session() -> None:
    fake = FakeTime()
    transport = _transport(fake)
    with aioresponses() as mocked:
        mocked.post(_QUOTA_URL, payload={"code": 0, "data": {}})
        await transport.request("getquota", {})
    owned = transport._owned_session
    assert owned is not None
    await transport.close()
    assert owned.closed


async def test_close_never_touches_an_external_session() -> None:
    async with aiohttp.ClientSession() as session:
        fake = FakeTime()
        transport = _transport(fake, session=session)
        await transport.close()
        assert not session.closed


async def test_external_session_is_used_for_requests() -> None:
    async with aiohttp.ClientSession() as session:
        fake = FakeTime()
        transport = _transport(fake, session=session)
        assert transport._session() is session
