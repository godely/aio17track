"""Frozen, slotted dataclasses for API inputs and outputs (SPEC §6).

Output models are built via their ``from_api`` classmethods; callers never
construct them from raw dicts themselves.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self

from .enums import ErrorCode, MainStatus, SubStatus, TrackingStatus

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
    """A number as 17track knows it after registration."""

    number: str
    carrier: int
    tracking_status: TrackingStatus
    package_status: MainStatus
    register_time: datetime | None
    tag: str | None
    order_no: str | None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Self:
        raise NotImplementedError


@dataclass(slots=True, frozen=True)
class TrackEvent:
    """One entry in a package's tracking history.

    Timestamp rule (SPEC §6): ``time_iso`` is parsed timezone-aware;
    ``time_raw`` is preserved verbatim — when ``sub_status`` is
    ``Delivered_Other`` the true delivery time lives there. Never fabricate
    a timezone the API did not send.
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
        raise NotImplementedError


@dataclass(slots=True, frozen=True)
class LatestStatus:
    """Current main + sub status of a package."""

    status: MainStatus
    sub_status: SubStatus

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Self:
        raise NotImplementedError


@dataclass(slots=True, frozen=True)
class TrackInfo:
    """Full tracking state of one number."""

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
        raise NotImplementedError


@dataclass(slots=True, frozen=True)
class Quota:
    """Credit balance; ``getquota`` is the only source of truth — never estimate locally."""

    remaining: int
    used: int | None
    total: int | None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Self:
        raise NotImplementedError


@dataclass(slots=True, frozen=True)
class RejectedItem:
    """A per-item rejection inside an otherwise successful call (SPEC §7, plane 2)."""

    number: str
    carrier: int | None
    error_code: ErrorCode
    error_message: str

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Self:
        raise NotImplementedError


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
        raise NotImplementedError

    @property
    def ok(self) -> bool:
        return not self.rejected


@dataclass(slots=True, frozen=True)
class TrackListPage:
    """One 40-item page of the registered-number list."""

    items: tuple[RegisteredNumber, ...]
    page_no: int
    page_total: int
    data_total: int

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Self:
        raise NotImplementedError


@dataclass(slots=True, frozen=True)
class StoppedNotice:
    """Payload of a TRACKING_STOPPED webhook event."""

    # TODO(M1): confirm the full field set against the v2.4 webhook docs.
    number: str
    carrier: int

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Self:
        raise NotImplementedError
