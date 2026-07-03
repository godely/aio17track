"""``Track17Client`` — the only façade callers touch (SPEC §5).

Design rules: no I/O in ``__init__``; every public method is async; callers
pass any number of items and the client chunks to 40 internally, dispatching
through the transport throttle and merging every chunk's accepted/rejected
items into a single ``BatchResult``.
"""

from collections.abc import Sequence

import aiohttp

from .enums import CacheLevel, MainStatus, TrackingStatus
from .models import (
    BatchResult,
    CarrierChange,
    InfoChange,
    NumberCarrier,
    Quota,
    RegisteredNumber,
    TrackInfo,
    TrackListPage,
    TrackRegistration,
)


class Track17Client:
    """Async client for the 17TRACK Tracking API v2.4 (header auth, ``17token``)."""

    def __init__(
        self,
        api_key: str,
        *,
        session: aiohttp.ClientSession | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        raise NotImplementedError

    # --- registration lifecycle ---

    async def register(self, items: Sequence[TrackRegistration]) -> BatchResult[RegisteredNumber]:
        raise NotImplementedError

    async def stop_track(self, items: Sequence[NumberCarrier]) -> BatchResult[NumberCarrier]:
        raise NotImplementedError

    async def retrack(self, items: Sequence[NumberCarrier]) -> BatchResult[NumberCarrier]:
        raise NotImplementedError

    async def delete_track(self, items: Sequence[NumberCarrier]) -> BatchResult[NumberCarrier]:
        raise NotImplementedError

    async def change_carrier(self, items: Sequence[CarrierChange]) -> BatchResult[NumberCarrier]:
        raise NotImplementedError

    async def change_info(self, items: Sequence[InfoChange]) -> BatchResult[NumberCarrier]:
        raise NotImplementedError

    # --- reads ---

    async def get_track_info(self, items: Sequence[NumberCarrier]) -> BatchResult[TrackInfo]:
        raise NotImplementedError

    async def get_track_list(
        self,
        *,
        number_filter: Sequence[str] | None = None,
        tracking_status: TrackingStatus | None = None,
        package_status: MainStatus | None = None,
        page_no: int = 1,
    ) -> TrackListPage:
        raise NotImplementedError

    async def get_quota(self) -> Quota:
        raise NotImplementedError

    # --- metered, guarded ---

    async def get_realtime_track_info(
        self,
        items: Sequence[NumberCarrier],
        *,
        cache_level: CacheLevel = CacheLevel.STANDARD,  # never defaults to INSTANT
    ) -> BatchResult[TrackInfo]:
        """Metered realtime lookup.

        Warning: ``CacheLevel.INSTANT`` deducts 10 credits per call
        (``CacheLevel.STANDARD`` deducts 1). INSTANT is never the default
        and must be opted into explicitly.
        """
        raise NotImplementedError

    async def close(self) -> None:
        """Close only a client-owned session (never an injected one)."""
        raise NotImplementedError

    async def __aenter__(self) -> "Track17Client":
        raise NotImplementedError

    async def __aexit__(self, *exc: object) -> None:
        raise NotImplementedError
