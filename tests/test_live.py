"""Opt-in live tests against the real 17TRACK API (SPEC §11.3).

Run explicitly with:

    SEVENTEENTRACK_LIVE_KEY=<your key> uv run pytest -m live

Excluded by default (pyproject addopts: -m 'not live'). A run costs one
tracking credit; the registered number is deleted at the end to return
the slot. Override the throwaway number via SEVENTEENTRACK_LIVE_NUMBER.
"""

import os

import aiohttp
import pytest

from aio17track import (
    ErrorCode,
    NumberCarrier,
    Quota,
    Track17Client,
    TrackRegistration,
)
from aio17track.carriers import CarrierCatalog

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("SEVENTEENTRACK_LIVE_KEY"),
        reason="SEVENTEENTRACK_LIVE_KEY is not set",
    ),
]

_CORREIOS = 2151


def _live_key() -> str:
    return os.environ["SEVENTEENTRACK_LIVE_KEY"]


def _throwaway_number() -> str:
    return os.environ.get("SEVENTEENTRACK_LIVE_NUMBER", "AA987654321BR")


async def test_register_read_quota_delete_roundtrip() -> None:
    """register -> get_track_info -> get_quota -> delete_track (SPEC §11.3)."""
    number = _throwaway_number()
    item = NumberCarrier(number, carrier=_CORREIOS)
    async with Track17Client(_live_key()) as client:
        registered = await client.register(
            [TrackRegistration(number=number, carrier=_CORREIOS, tag="aio17track-live")]
        )
        # A rerun after an aborted cleanup may find the number registered.
        assert registered.accepted or registered.already_registered
        try:
            info = await client.get_track_info([item])
            # A fabricated number has no events yet: either an accepted item
            # or a NO_TRACKING_INFO_YET rejection is a correct live answer.
            assert [entry.number for entry in info.accepted] == [number] or [
                entry.error_code for entry in info.rejected
            ] == [ErrorCode.NO_TRACKING_INFO_YET]

            quota = await client.get_quota()
            assert isinstance(quota, Quota)
            assert quota.remaining >= 0
        finally:
            deleted = await client.delete_track([item])
            assert [entry.number for entry in deleted.accepted] == [number]


async def test_carrier_catalog_resolves_api_codes() -> None:
    """The apicarrier list must contain the codes this suite relies on."""
    catalog = CarrierCatalog()
    async with aiohttp.ClientSession() as session:
        await catalog.load(session)
    assert catalog.name(_CORREIOS) is not None
    assert catalog.name(190012) is not None  # YanWen
    assert catalog.code("correios") == _CORREIOS
