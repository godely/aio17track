"""Track17Client mutations: register, stop/retrack/delete, change_* (SPEC §13 M4)."""

from collections.abc import Callable
from typing import Any

import pytest
from aioresponses import CallbackResult, aioresponses
from aioresponses.core import RequestCall
from yarl import URL

from aio17track import (
    BatchResult,
    CarrierChange,
    ErrorCode,
    InfoChange,
    MainStatus,
    NumberCarrier,
    Track17Client,
    TrackingStatus,
    TrackRegistration,
)

type FixtureLoader = Callable[[str], dict[str, Any]]

_BASE = "https://api.17track.net/track/v2.4"


def _url(endpoint: str) -> str:
    return f"{_BASE}/{endpoint}"


def _calls(mocked: aioresponses, endpoint: str) -> list[RequestCall]:
    return mocked.requests[("POST", URL(_url(endpoint)))]


def _sent_json(call: RequestCall) -> Any:
    return call.kwargs["json"]


def _echo_accepted(url: URL, **kwargs: Any) -> CallbackResult:
    sent = kwargs["json"]
    accepted = [{"number": item["number"], "carrier": item.get("carrier", 0)} for item in sent]
    return CallbackResult(payload={"code": 0, "data": {"accepted": accepted, "rejected": []}})


# --- register ---


async def test_register_payload_omits_unset_fields() -> None:
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_url("register"), callback=_echo_accepted)
            await client.register(
                [
                    TrackRegistration(
                        number="AA123456789BR", carrier=2151, tag="order-1042", lang="en"
                    ),
                    TrackRegistration(number="YT26169"),
                ]
            )
            sent = _sent_json(_calls(mocked, "register")[0])
    assert sent == [
        {"number": "AA123456789BR", "carrier": 2151, "tag": "order-1042", "lang": "en"},
        {"number": "YT26169"},
    ]


async def test_register_parses_mixed_response(load_fixture: FixtureLoader) -> None:
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_url("register"), payload=load_fixture("register_mixed"))
            result = await client.register([TrackRegistration(number="AA123456789BR")])

    assert len(result.accepted) == 1
    registered = result.accepted[0]
    assert registered.number == "AA123456789BR"
    assert registered.carrier == 2151
    assert registered.tag == "order-1042"
    # register accepted items carry no status fields (M1 decision): UNKNOWN.
    assert registered.tracking_status is TrackingStatus.UNKNOWN
    assert registered.package_status is MainStatus.UNKNOWN
    assert len(result.rejected) == 1
    assert result.rejected[0].error_code is ErrorCode.INVALID_DATA_FORMAT
    assert not result.ok


async def test_register_already_registered_view(load_fixture: FixtureLoader) -> None:
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_url("register"), payload=load_fixture("register_already_registered"))
            result = await client.register([TrackRegistration(number="AA123456789BR")])

    assert result.accepted == ()
    assert len(result.already_registered) == 1
    assert result.already_registered[0].number == "AA123456789BR"


async def test_register_chunks_at_40() -> None:
    numbers = [f"PKG{i:04d}" for i in range(85)]
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_url("register"), callback=_echo_accepted, repeat=True)
            result = await client.register([TrackRegistration(number=n) for n in numbers])
            assert [len(_sent_json(call)) for call in _calls(mocked, "register")] == [40, 40, 5]
    assert [item.number for item in result.accepted] == numbers


# --- stop / retrack / delete ---


@pytest.mark.parametrize(
    ("method_name", "endpoint"),
    [("stop_track", "stoptrack"), ("retrack", "retrack"), ("delete_track", "deletetrack")],
)
async def test_lifecycle_mutations_send_number_carrier_payloads(
    method_name: str, endpoint: str
) -> None:
    items = [NumberCarrier("AA123456789BR", carrier=2151), NumberCarrier("YT26169")]
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_url(endpoint), callback=_echo_accepted)
            method = getattr(client, method_name)
            result: BatchResult[NumberCarrier] = await method(items)
            sent = _sent_json(_calls(mocked, endpoint)[0])

    assert sent == [{"number": "AA123456789BR", "carrier": 2151}, {"number": "YT26169"}]
    assert result.accepted == (
        NumberCarrier("AA123456789BR", carrier=2151),
        NumberCarrier("YT26169", carrier=0),
    )
    assert result.ok


async def test_stop_track_rejection_parses_error(load_fixture: FixtureLoader) -> None:
    payload = {
        "code": 0,
        "data": {
            "accepted": [],
            "rejected": [
                {
                    "number": "AA123456789BR",
                    "carrier": 2151,
                    "error": {
                        "code": -18019906,
                        "message": "Only numbers being tracked can be stopped.",
                    },
                }
            ],
        },
    }
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_url("stoptrack"), payload=payload)
            result = await client.stop_track([NumberCarrier("AA123456789BR", carrier=2151)])

    assert result.rejected[0].error_code is ErrorCode.STOP_ONLY_TRACKING
    assert result.rejected[0].carrier == 2151


# --- change_carrier / change_info ---


async def test_change_carrier_payload_and_parse() -> None:
    response = {
        "code": 0,
        "data": {
            "accepted": [{"number": "AA123456789BR", "carrier_new": 190012}],
            "rejected": [],
        },
    }
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_url("changecarrier"), payload=response)
            result = await client.change_carrier(
                [CarrierChange(number="AA123456789BR", carrier_old=2151, carrier_new=190012)]
            )
            sent = _sent_json(_calls(mocked, "changecarrier")[0])

    assert sent == [{"number": "AA123456789BR", "carrier_old": 2151, "carrier_new": 190012}]
    # accepted item carried carrier_new; NumberCarrier.from_api picks it up
    assert result.accepted == (NumberCarrier("AA123456789BR", carrier=190012),)


async def test_change_info_payload_omits_unset_fields() -> None:
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_url("changeinfo"), callback=_echo_accepted)
            await client.change_info(
                [
                    InfoChange(number="AA123456789BR", carrier=2151, tag="new-tag"),
                    InfoChange(number="YT26169", order_no="86574382938"),
                ]
            )
            sent = _sent_json(_calls(mocked, "changeinfo")[0])
    assert sent == [
        {"number": "AA123456789BR", "carrier": 2151, "tag": "new-tag"},
        {"number": "YT26169", "order_no": "86574382938"},
    ]


# --- batching contract shared by all mutations ---


@pytest.mark.parametrize(
    "method_name", ["stop_track", "retrack", "delete_track"]
)
async def test_lifecycle_mutations_with_no_items_make_no_requests(method_name: str) -> None:
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            method = getattr(client, method_name)
            result: BatchResult[NumberCarrier] = await method([])
            assert not mocked.requests
    assert result.ok
