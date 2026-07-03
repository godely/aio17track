"""``Track17Client`` — the only façade callers touch (SPEC §5).

Design rules: no I/O in ``__init__``; every public method is async; callers
pass any number of items and the client chunks to 40 internally, dispatching
through the transport throttle and merging every chunk's accepted/rejected
items into a single ``BatchResult``.
"""

from collections.abc import Callable, Iterator, Sequence
from typing import Any

import aiohttp

from .enums import CacheLevel, MainStatus, TrackingStatus
from .models import (
    BatchResult,
    CarrierChange,
    InfoChange,
    NumberCarrier,
    Quota,
    RegisteredNumber,
    RejectedItem,
    TrackInfo,
    TrackListPage,
    TrackRegistration,
)
from .transport import _Transport

_BATCH_LIMIT = 40  # mutation/read-by-number endpoints (SPEC §2)
_LIST_FILTER_LIMIT = 200  # gettracklist number filter (SPEC §2)


def _chunk[T](items: Sequence[T], size: int = _BATCH_LIMIT) -> Iterator[Sequence[T]]:
    """Split ``items`` into consecutive chunks of at most ``size``, in order."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _number_carrier_payload(item: NumberCarrier) -> dict[str, object]:
    payload: dict[str, object] = {"number": item.number}
    if item.carrier is not None:
        payload["carrier"] = item.carrier
    return payload


def _registration_payload(item: TrackRegistration) -> dict[str, object]:
    payload: dict[str, object] = {"number": item.number}
    if item.carrier is not None:
        payload["carrier"] = item.carrier
    if item.tag is not None:
        payload["tag"] = item.tag
    if item.order_no is not None:
        payload["order_no"] = item.order_no
    if item.lang is not None:
        payload["lang"] = item.lang
    if item.param is not None:
        # TODO(M6): confirm against the live API — the v2.4 register doc lists
        # dedicated fields (destination_postal_code, phone_number, ...) rather
        # than a generic "param", but responses still carry "param".
        payload["param"] = item.param
    return payload


def _carrier_change_payload(item: CarrierChange) -> dict[str, object]:
    return {
        "number": item.number,
        "carrier_old": item.carrier_old,
        "carrier_new": item.carrier_new,
    }


def _info_change_payload(item: InfoChange) -> dict[str, object]:
    payload: dict[str, object] = {"number": item.number}
    if item.carrier is not None:
        payload["carrier"] = item.carrier
    if item.tag is not None:
        payload["tag"] = item.tag
    if item.order_no is not None:
        payload["order_no"] = item.order_no
    return payload


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
        self._transport = _Transport(
            api_key, session=session, timeout=timeout, max_retries=max_retries
        )

    async def _batched[R](
        self,
        endpoint: str,
        payloads: Sequence[dict[str, object]],
        parse: Callable[[dict[str, Any]], R],
    ) -> BatchResult[R]:
        """Dispatch ``payloads`` in chunks of 40 and merge into one result.

        Chunks go through the transport throttle sequentially; accepted and
        rejected items are concatenated across chunks in dispatch order.
        """
        accepted: list[R] = []
        rejected: list[RejectedItem] = []
        for chunk in _chunk(payloads):
            data = await self._transport.request(endpoint, list(chunk))
            accepted.extend(parse(item) for item in data.get("accepted") or [])
            rejected.extend(RejectedItem.from_api(item) for item in data.get("rejected") or [])
        return BatchResult(accepted=tuple(accepted), rejected=tuple(rejected))

    # --- registration lifecycle ---

    async def register(self, items: Sequence[TrackRegistration]) -> BatchResult[RegisteredNumber]:
        """Register numbers for tracking (1 credit per successful registration).

        An already-registered number lands in ``rejected`` with
        ``ErrorCode.ALREADY_REGISTERED``; see ``BatchResult.already_registered``.
        """
        return await self._batched(
            "register",
            [_registration_payload(item) for item in items],
            RegisteredNumber.from_api,
        )

    async def stop_track(self, items: Sequence[NumberCarrier]) -> BatchResult[NumberCarrier]:
        return await self._batched(
            "stoptrack",
            [_number_carrier_payload(item) for item in items],
            NumberCarrier.from_api,
        )

    async def retrack(self, items: Sequence[NumberCarrier]) -> BatchResult[NumberCarrier]:
        """Restart tracking for stopped numbers (each number can retrack once)."""
        return await self._batched(
            "retrack",
            [_number_carrier_payload(item) for item in items],
            NumberCarrier.from_api,
        )

    async def delete_track(self, items: Sequence[NumberCarrier]) -> BatchResult[NumberCarrier]:
        return await self._batched(
            "deletetrack",
            [_number_carrier_payload(item) for item in items],
            NumberCarrier.from_api,
        )

    async def change_carrier(self, items: Sequence[CarrierChange]) -> BatchResult[NumberCarrier]:
        return await self._batched(
            "changecarrier",
            [_carrier_change_payload(item) for item in items],
            NumberCarrier.from_api,
        )

    async def change_info(self, items: Sequence[InfoChange]) -> BatchResult[NumberCarrier]:
        return await self._batched(
            "changeinfo",
            [_info_change_payload(item) for item in items],
            NumberCarrier.from_api,
        )

    # --- reads ---

    async def get_track_info(self, items: Sequence[NumberCarrier]) -> BatchResult[TrackInfo]:
        return await self._batched(
            "gettrackinfo",
            [_number_carrier_payload(item) for item in items],
            TrackInfo.from_api,
        )

    async def get_track_list(
        self,
        *,
        number_filter: Sequence[str] | None = None,
        tracking_status: TrackingStatus | None = None,
        package_status: MainStatus | None = None,
        page_no: int = 1,
    ) -> TrackListPage:
        if number_filter is not None and len(number_filter) > _LIST_FILTER_LIMIT:
            raise ValueError(
                f"gettracklist accepts at most {_LIST_FILTER_LIMIT} numbers as a "
                f"filter; got {len(number_filter)}"
            )
        payload: dict[str, object] = {"page_no": page_no}
        if number_filter:
            payload["number"] = ",".join(number_filter)
        if tracking_status is not None:
            payload["tracking_status"] = tracking_status.value
        if package_status is not None:
            payload["package_status"] = package_status.value
        envelope = await self._transport.request_envelope("gettracklist", payload)
        return TrackListPage.from_api(envelope)

    async def get_quota(self) -> Quota:
        data = await self._transport.request("getquota", None)
        return Quota.from_api(data)

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
        raise NotImplementedError  # M6

    async def close(self) -> None:
        """Close only a client-owned session (never an injected one)."""
        await self._transport.close()

    async def __aenter__(self) -> "Track17Client":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
