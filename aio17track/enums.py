"""Status, push, cache-level, and error-code enums for the 17TRACK v2.4 API (SPEC §8).

Forward-compatibility rule (non-negotiable): every enum parsed from an API
payload falls back to its UNKNOWN member instead of raising. The fallback
machinery that carries the raw string/int lands in M1.
"""

from enum import Enum, IntEnum, StrEnum


class MainStatus(StrEnum):
    """Package main status (`latest_status.status`)."""

    # TODO(M1): populate from v2.4 docs (9 main statuses at time of writing;
    # the count has changed between versions, so do not hardcode from memory).
    UNKNOWN = "UNKNOWN"
    NOT_FOUND = "NotFound"
    IN_TRANSIT = "InTransit"
    DELIVERED = "Delivered"


class SubStatus(StrEnum):
    """Package sub-status (`latest_status.sub_status`)."""

    # TODO(M1): populate from v2.4 docs (~30 sub-statuses at time of writing).
    UNKNOWN = "UNKNOWN"
    DELIVERED_OTHER = "Delivered_Other"
    EXCEPTION_RETURNING = "Exception_Returning"


class TrackingStatus(StrEnum):
    """Whether 17track is still tracking the number."""

    # TODO(M1): populate from v2.4 docs.
    UNKNOWN = "UNKNOWN"
    TRACKING = "Tracking"
    STOPPED = "Stopped"


class PushStatus(StrEnum):
    """Webhook delivery outcome."""

    # TODO(M1): populate from v2.4 docs.
    UNKNOWN = "UNKNOWN"
    SUCCESS = "Success"
    FAILURE = "Failure"


class CacheLevel(Enum):
    """Cache level for ``Track17Client.get_realtime_track_info``.

    Warning: INSTANT deducts 10 credits per call (STANDARD deducts 1).
    INSTANT must never be the default anywhere.
    """

    # TODO(M1): confirm wire values against the v2.4 docs.
    UNKNOWN = "UNKNOWN"
    STANDARD = "standard"
    INSTANT = "instant"


class ErrorCode(IntEnum):
    """Documented 17track error codes, request-level and per-item (SPEC §7).

    Unknown codes map to UNKNOWN, carrying the raw int (fallback machinery in M1).
    Members below are the codes SPEC §7 names explicitly.
    """

    # TODO(M1): populate the full documented code table (~45 codes) from v2.4 docs.
    UNKNOWN = 0
    IP_NOT_WHITELISTED = -18010001
    AUTHENTICATION_FAILED = -18010002
    ACCOUNT_DISABLED = -18010004
    INVALID_CARRIER = -18010013
    ALREADY_REGISTERED = -18019901
    RETRACK_NOT_ALLOWED = -18019902
    QUOTA_EXHAUSTED = -18019907
    QUOTA_LIMIT_REACHED = -18019908
