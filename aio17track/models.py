"""Frozen, slotted dataclasses for API inputs and outputs (SPEC §6).

Output models are built via their ``from_api`` classmethods; callers never
construct them from raw dicts themselves. Field mappings follow the v2.4
docs (captured 2026-07-03). Empty strings from the API are normalized to
``None`` for optional string fields.

Timestamp rule (SPEC §6): ISO strings parse via ``datetime.fromisoformat``
and are timezone-aware exactly when the API sent an offset — a timezone is
never fabricated. The wire ``time_raw`` is an object
``{date, time, timezone}`` (each nullable); it is flattened into a
space-joined string of its non-null parts to fit the ``str | None`` field,
losing nothing the API sent.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self

from .enums import ErrorCode, MainStatus, SubStatus, TrackingStatus

# --- parsing helpers (internal) ---


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value) or None


def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _time_raw_str(value: Any) -> str | None:
    """Flatten the wire ``time_raw`` object into a string, verbatim parts only."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        parts = (value.get("date"), value.get("time"), value.get("timezone"))
        joined = " ".join(str(part) for part in parts if part)
        return joined or None
    return None


def _parse_coordinates(value: Any) -> tuple[float, float] | None:
    """Parse a ``{longitude, latitude}`` object into ``(latitude, longitude)``."""
    if not isinstance(value, dict):
        return None
    latitude, longitude = value.get("latitude"), value.get("longitude")
    if latitude is None or longitude is None:
        return None
    try:
        return (float(latitude), float(longitude))
    except (TypeError, ValueError):
        return None


def _parse_sub_status(value: Any) -> SubStatus:
    return SubStatus(value) if value else SubStatus.UNKNOWN


# --- inputs ---


@dataclass(slots=True, frozen=True)
class TrackRegistration:
    """One number to register for tracking."""

    number: str
    carrier: int | None = None  # omit -> 17track auto-detects
    tag: str | None = None
    order_no: str | None = None
    lang: str | None = None
    param: str | None = None  # extra carrier param (phone/zip), when required


@dataclass(slots=True, frozen=True)
class NumberCarrier:
    """A tracking number plus optional carrier code."""

    number: str
    carrier: int | None = None


@dataclass(slots=True, frozen=True)
class CarrierChange:
    """Reassign a registered number from one carrier to another."""

    number: str
    carrier_old: int
    carrier_new: int


@dataclass(slots=True, frozen=True)
class InfoChange:
    """Update tag / order metadata on a registered number."""

    number: str
    carrier: int | None = None
    tag: str | None = None
    order_no: str | None = None


# --- outputs ---


@dataclass(slots=True, frozen=True)
class RegisteredNumber:
    """A number as 17track knows it after registration.

    Built from ``gettracklist`` items (all fields present) and ``register``
    accepted items (which carry only number/carrier/tag — the missing
    status fields land as UNKNOWN / None there).
    """

    number: str
    carrier: int
    tracking_status: TrackingStatus
    package_status: MainStatus
    register_time: datetime | None
    tag: str | None
    order_no: str | None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Self:
        tracking_status = raw.get("tracking_status")
        package_status = raw.get("package_status")
        return cls(
            number=str(raw["number"]),
            carrier=int(raw.get("carrier") or 0),
            tracking_status=(
                TrackingStatus(tracking_status) if tracking_status else TrackingStatus.UNKNOWN
            ),
            package_status=MainStatus(package_status) if package_status else MainStatus.UNKNOWN,
            register_time=_parse_datetime(raw.get("register_time")),
            tag=_opt_str(raw.get("tag")),
            order_no=_opt_str(raw.get("order_no")),
        )


@dataclass(slots=True, frozen=True)
class TrackEvent:
    """One entry in a package's tracking history.

    ``time_raw`` preserves the wire object's non-null parts verbatim
    (space-joined); per the docs, when ``sub_status`` is ``Delivered_Other``
    the true delivery time lives here. ``coordinates`` is ``(latitude,
    longitude)``, taken from the event's ``address.coordinates``.
    """

    time_iso: datetime | None
    time_raw: str | None
    description: str | None
    location: str | None
    stage: str | None
    sub_status: SubStatus
    coordinates: tuple[float, float] | None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Self:
        address = raw.get("address")
        coordinates_raw = address.get("coordinates") if isinstance(address, dict) else None
        return cls(
            time_iso=_parse_datetime(raw.get("time_iso")),
            time_raw=_time_raw_str(raw.get("time_raw")),
            description=_opt_str(raw.get("description")),
            location=_opt_str(raw.get("location")),
            stage=_opt_str(raw.get("stage")),
            sub_status=_parse_sub_status(raw.get("sub_status")),
            coordinates=_parse_coordinates(coordinates_raw),
        )


@dataclass(slots=True, frozen=True)
class LatestStatus:
    """Current main + sub status of a package."""

    status: MainStatus
    sub_status: SubStatus

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Self:
        status = raw.get("status")
        return cls(
            status=MainStatus(status) if status else MainStatus.UNKNOWN,
            sub_status=_parse_sub_status(raw.get("sub_status")),
        )


# track_info sections mapped onto typed fields; everything else (time_metrics,
# milestone, misc_info, and anything version-new) flows into
# TrackInfo.tracking_number_extra untouched.
_MODELED_TRACK_INFO_KEYS = frozenset({"shipping_info", "latest_status", "latest_event", "tracking"})


@dataclass(slots=True, frozen=True)
class TrackInfo:
    """Full tracking state of one number.

    Built from a ``gettrackinfo`` / ``getRealTimeTrackInfo`` accepted item,
    or a ``TRACKING_UPDATED`` webhook ``data`` object (same shape).

    ``events`` concatenates ``tracking.providers[*].events`` in API order —
    the docs put the most recent provider first, so this is newest-first.
    ``stopped``: the v2.4 payload carries no tracking-status flag, so this
    is False unless the payload includes ``tracking_status == "Stopped"``.
    """

    number: str
    carrier: int
    latest_status: LatestStatus
    latest_event: TrackEvent | None
    events: tuple[TrackEvent, ...]  # full history, newest-first
    shipping_country: str | None
    recipient_country: str | None
    tracking_number_extra: dict[str, Any]  # anything version-new we don't model yet
    stopped: bool

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Self:
        track_info = raw.get("track_info") or {}
        shipping_info = track_info.get("shipping_info") or {}
        shipper_address = shipping_info.get("shipper_address") or {}
        recipient_address = shipping_info.get("recipient_address") or {}
        latest_event_raw = track_info.get("latest_event")
        tracking = track_info.get("tracking") or {}
        events = tuple(
            TrackEvent.from_api(event)
            for provider in tracking.get("providers") or []
            for event in provider.get("events") or []
        )
        extra = {
            key: value
            for key, value in track_info.items()
            if key not in _MODELED_TRACK_INFO_KEYS
        }
        return cls(
            number=str(raw["number"]),
            carrier=int(raw.get("carrier") or 0),
            latest_status=LatestStatus.from_api(track_info.get("latest_status") or {}),
            latest_event=TrackEvent.from_api(latest_event_raw) if latest_event_raw else None,
            events=events,
            shipping_country=_opt_str(shipper_address.get("country")),
            recipient_country=_opt_str(recipient_address.get("country")),
            tracking_number_extra=extra,
            stopped=raw.get("tracking_status") == TrackingStatus.STOPPED.value,
        )


@dataclass(slots=True, frozen=True)
class Quota:
    """Credit balance; ``getquota`` is the only source of truth — never estimate locally."""

    remaining: int
    used: int | None
    total: int | None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Self:
        return cls(
            remaining=int(raw["quota_remain"]),
            used=_opt_int(raw.get("quota_used")),
            total=_opt_int(raw.get("quota_total")),
        )


@dataclass(slots=True, frozen=True)
class RejectedItem:
    """A per-item rejection inside an otherwise successful call (SPEC §7, plane 2)."""

    number: str
    carrier: int | None
    error_code: ErrorCode
    error_message: str

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Self:
        error = raw.get("error") or {}
        code = error.get("code")
        return cls(
            number=str(raw.get("number") or ""),
            # the API has been seen sending carrier as a string here
            carrier=_opt_int(raw.get("carrier")),
            error_code=ErrorCode(code) if isinstance(code, int) else ErrorCode.UNKNOWN,
            error_message=str(error.get("message") or ""),
        )


@dataclass(slots=True, frozen=True)
class BatchResult[T]:
    """Merged accepted/rejected outcome of a batched call.

    Partial success is normal: it is data, not an exception.
    """

    accepted: tuple[T, ...]
    rejected: tuple[RejectedItem, ...]

    @property
    def already_registered(self) -> tuple[RejectedItem, ...]:
        """Rejections whose code == ErrorCode.ALREADY_REGISTERED (-18019901).

        For an HA integration, "already registered" is success, not failure.
        """
        return tuple(
            item for item in self.rejected if item.error_code is ErrorCode.ALREADY_REGISTERED
        )

    @property
    def ok(self) -> bool:
        return not self.rejected


@dataclass(slots=True, frozen=True)
class TrackListPage:
    """One 40-item page of the registered-number list.

    Note: on the wire, ``page`` is a *sibling* of ``code``/``data`` in the
    envelope, so ``from_api`` accepts the full response envelope.
    """

    items: tuple[RegisteredNumber, ...]
    page_no: int
    page_total: int
    data_total: int

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Self:
        page = raw.get("page") or {}
        data = raw.get("data") or {}
        accepted = data.get("accepted")
        if accepted is None:
            accepted = raw.get("accepted") or []
        items = tuple(RegisteredNumber.from_api(item) for item in accepted)
        page_no = page.get("page_no")
        page_total = page.get("page_total")
        data_total = page.get("data_total")
        return cls(
            items=items,
            page_no=int(page_no) if page_no is not None else 1,
            page_total=int(page_total) if page_total is not None else 1,
            data_total=int(data_total) if data_total is not None else len(items),
        )


@dataclass(slots=True, frozen=True)
class StoppedNotice:
    """Payload of a TRACKING_STOPPED webhook event (number/carrier/param/tag)."""

    number: str
    carrier: int
    param: str | None = None
    tag: str | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Self:
        return cls(
            number=str(raw["number"]),
            carrier=int(raw.get("carrier") or 0),
            param=_opt_str(raw.get("param")),
            tag=_opt_str(raw.get("tag")),
        )
