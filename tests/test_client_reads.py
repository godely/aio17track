"""Track17Client reads: get_quota, get_track_info, get_track_list (SPEC §5, §13 M3).

HTTP is mocked with aioresponses. Chunked tests stay within the 3-request
throttle burst so no real time passes.
"""

import math
from collections.abc import Callable
from typing import Any

import aiohttp
import pytest
from aioresponses import CallbackResult, aioresponses
from aioresponses.core import RequestCall
from yarl import URL

from aio17track import (
    ErrorCode,
    MainStatus,
    NumberCarrier,
    Track17Client,
    TrackingStatus,
)
from aio17track.client import _chunk

type FixtureLoader = Callable[[str], dict[str, Any]]

_QUOTA_URL = "https://api.17track.net/track/v2.4/getquota"
_INFO_URL = "https://api.17track.net/track/v2.4/gettrackinfo"
_LIST_URL = "https://api.17track.net/track/v2.4/gettracklist"


def _calls(mocked: aioresponses, url: str) -> list[RequestCall]:
    return mocked.requests[("POST", URL(url))]


def _sent_json(call: RequestCall) -> Any:
    return call.kwargs["json"]


def _echo_accepted(url: URL, **kwargs: Any) -> CallbackResult:
    """Accept every submitted number, echoing it back in order."""
    sent = kwargs["json"]
    accepted = [{"number": item["number"], "carrier": item.get("carrier", 0)} for item in sent]
    return CallbackResult(payload={"code": 0, "data": {"accepted": accepted, "rejected": []}})


# --- chunking property (SPEC §11.2) ---


@pytest.mark.parametrize("n", [0, 1, 39, 40, 41, 80, 85, 200, 1000])
def test_chunking_property(n: int) -> None:
    """N items always split into ceil(N/40) chunks, all preserved, order stable."""
    items = [f"N{i:04d}" for i in range(n)]
    chunks = list(_chunk(items))
    assert len(chunks) == math.ceil(n / 40)
    assert all(len(chunk) <= 40 for chunk in chunks)
    assert [item for chunk in chunks for item in chunk] == items


# --- get_quota ---


async def test_get_quota(load_fixture: FixtureLoader) -> None:
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_QUOTA_URL, payload=load_fixture("getquota"))
            quota = await client.get_quota()
    assert quota.remaining == 1098
    assert quota.used == 2
    assert quota.total == 1100


# --- get_track_info ---


async def test_get_track_info_payload_omits_null_carrier() -> None:
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_INFO_URL, callback=_echo_accepted)
            await client.get_track_info(
                [NumberCarrier("AA123456789BR", carrier=2151), NumberCarrier("YT26169")]
            )
            sent = _sent_json(_calls(mocked, _INFO_URL)[0])
    assert sent == [{"number": "AA123456789BR", "carrier": 2151}, {"number": "YT26169"}]


async def test_get_track_info_splits_accepted_and_rejected(
    load_fixture: FixtureLoader,
) -> None:
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_INFO_URL, payload=load_fixture("gettrackinfo_yanwen_190012"))
            result = await client.get_track_info([NumberCarrier("YT2616912345678901")])

    assert len(result.accepted) == 1
    assert result.accepted[0].number == "YT2616912345678901"
    assert result.accepted[0].latest_status.status is MainStatus.IN_TRANSIT
    assert len(result.rejected) == 1
    assert result.rejected[0].error_code is ErrorCode.NOT_REGISTERED
    assert not result.ok


async def test_get_track_info_chunks_at_40_and_merges_in_order() -> None:
    numbers = [f"PKG{i:04d}" for i in range(85)]
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_INFO_URL, callback=_echo_accepted, repeat=True)
            result = await client.get_track_info([NumberCarrier(n) for n in numbers])

            calls = _calls(mocked, _INFO_URL)
            assert [len(_sent_json(call)) for call in calls] == [40, 40, 5]
    assert [info.number for info in result.accepted] == numbers
    assert result.ok


async def test_get_track_info_with_no_items_makes_no_requests() -> None:
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            result = await client.get_track_info([])
            assert not mocked.requests
    assert result.accepted == ()
    assert result.rejected == ()
    assert result.ok


# --- get_track_list ---


async def test_get_track_list_sends_filters_and_parses_page(
    load_fixture: FixtureLoader,
) -> None:
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_LIST_URL, payload=load_fixture("gettracklist_page"))
            page = await client.get_track_list(
                number_filter=["AA123456789BR", "YT2616912345678901"],
                tracking_status=TrackingStatus.TRACKING,
                package_status=MainStatus.DELIVERED,
                page_no=1,
            )
            sent = _sent_json(_calls(mocked, _LIST_URL)[0])

    assert sent == {
        "page_no": 1,
        "number": "AA123456789BR,YT2616912345678901",
        "tracking_status": "Tracking",
        "package_status": "Delivered",
    }
    assert page.page_total == 2
    assert page.data_total == 43
    assert page.items[0].tracking_status is TrackingStatus.TRACKING


async def test_get_track_list_omits_unset_filters(load_fixture: FixtureLoader) -> None:
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            mocked.post(_LIST_URL, payload=load_fixture("gettracklist_page"))
            await client.get_track_list(page_no=3)
            sent = _sent_json(_calls(mocked, _LIST_URL)[0])
    assert sent == {"page_no": 3}


async def test_get_track_list_rejects_more_than_200_filter_numbers() -> None:
    async with Track17Client("test-key") as client:
        with aioresponses() as mocked:
            with pytest.raises(ValueError, match="200"):
                await client.get_track_list(number_filter=[f"N{i}" for i in range(201)])
            assert not mocked.requests  # fails before any HTTP


# --- lifecycle ---


async def test_context_manager_closes_owned_session(load_fixture: FixtureLoader) -> None:
    client = Track17Client("test-key")
    async with client:
        with aioresponses() as mocked:
            mocked.post(_QUOTA_URL, payload=load_fixture("getquota"))
            await client.get_quota()
        owned = client._transport._owned_session
        assert owned is not None
    assert owned.closed


async def test_context_manager_leaves_external_session_open(
    load_fixture: FixtureLoader,
) -> None:
    async with aiohttp.ClientSession() as session:
        async with Track17Client("test-key", session=session) as client:
            with aioresponses() as mocked:
                mocked.post(_QUOTA_URL, payload=load_fixture("getquota"))
                await client.get_quota()
        assert not session.closed


async def test_client_init_does_no_io() -> None:
    """No session, no connections — SPEC §5: no I/O in __init__."""
    client = Track17Client("test-key")
    assert client._transport._owned_session is None
