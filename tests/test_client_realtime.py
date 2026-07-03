"""get_realtime_track_info: guarded, metered, one number per request (SPEC §5, §13 M6)."""

from collections.abc import Callable
from typing import Any

import pytest
from aioresponses import CallbackResult, aioresponses
from aioresponses.core import RequestCall
from yarl import URL

from aio17track import (
    CacheLevel,
    ErrorCode,
    MainStatus,
    NumberCarrier,
    Track17Client,
)

type FixtureLoader = Callable[[str], dict[str, Any]]

_REALTIME_URL = "https://api.17track.net/track/v2.4/getRealTimeTrackInfo"


def _calls(mocked: aioresponses, url: str) -> list[RequestCall]:
    return mocked.requests[("POST", URL(url))]


def _sent_json(call: RequestCall) -> Any:
    return call.kwargs["json"]


def _echo_accepted(url: URL, **kwargs: Any) -> CallbackResult:
    sent = kwargs["json"]
    accepted = [{"number": item["number"], "carrier": item.get("carrier", 0)} for item in sent]
    return CallbackResult(payload={"code": 0, "data": {"accepted": accepted, "rejected": []}})


async def test_one_request_per_number() -> None:
    """The realtime endpoint accepts a single number per request."""
    items = [NumberCarrier(f"PKG{i}", carrier=2151) for i in range(3)]
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_REALTIME_URL, callback=_echo_accepted, repeat=True)
            result = await client.get_realtime_track_info(items)

            calls = _calls(mocked, _REALTIME_URL)
            assert [len(_sent_json(call)) for call in calls] == [1, 1, 1]
    assert [info.number for info in result.accepted] == ["PKG0", "PKG1", "PKG2"]


async def test_standard_omits_cache_level_from_the_wire() -> None:
    """The docs conflict on cacheLevel's wire type; STANDARD (the documented
    default) is omitted entirely so the ambiguity cannot bite."""
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_REALTIME_URL, callback=_echo_accepted)
            await client.get_realtime_track_info([NumberCarrier("PKG0", carrier=2151)])
            sent = _sent_json(_calls(mocked, _REALTIME_URL)[0])
    assert sent == [{"number": "PKG0", "carrier": 2151}]


async def test_instant_is_sent_explicitly() -> None:
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_REALTIME_URL, callback=_echo_accepted)
            await client.get_realtime_track_info(
                [NumberCarrier("PKG0", carrier=2151)], cache_level=CacheLevel.INSTANT
            )
            sent = _sent_json(_calls(mocked, _REALTIME_URL)[0])
    assert sent == [{"number": "PKG0", "carrier": 2151, "cacheLevel": "Instant"}]


async def test_unknown_cache_level_is_rejected() -> None:
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            with pytest.raises(ValueError, match="cache_level"):
                await client.get_realtime_track_info(
                    [NumberCarrier("PKG0")], cache_level=CacheLevel.UNKNOWN
                )
            assert not mocked.requests


async def test_realtime_parses_full_track_info(load_fixture: FixtureLoader) -> None:
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_REALTIME_URL, payload=load_fixture("gettrackinfo_correios_2151"))
            result = await client.get_realtime_track_info(
                [NumberCarrier("AA123456789BR", carrier=2151)]
            )
    assert result.accepted[0].latest_status.status is MainStatus.DELIVERED


async def test_realtime_rejections_merge_across_calls() -> None:
    def alternating(url: URL, **kwargs: Any) -> CallbackResult:
        sent = kwargs["json"]
        number = sent[0]["number"]
        if number == "BAD":
            return CallbackResult(
                payload={
                    "code": 0,
                    "data": {
                        "accepted": [],
                        "rejected": [
                            {
                                "number": number,
                                "carrier": 21051,
                                "error": {
                                    "code": -18019818,
                                    "message": "The carrier is not supported.",
                                },
                            }
                        ],
                    },
                }
            )
        return _echo_accepted(url, **kwargs)

    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_REALTIME_URL, callback=alternating, repeat=True)
            result = await client.get_realtime_track_info(
                [NumberCarrier("GOOD", carrier=2151), NumberCarrier("BAD", carrier=21051)]
            )

    assert [info.number for info in result.accepted] == ["GOOD"]
    assert result.rejected[0].error_code is ErrorCode.REALTIME_CARRIER_UNSUPPORTED
    assert not result.ok
