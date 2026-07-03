"""aio17track — dependency-light async client for the 17TRACK Tracking API v2.4.

Public exports only; SPEC.md is the source of truth.
"""

from .client import Track17Client
from .enums import (
    CacheLevel,
    ErrorCode,
    MainStatus,
    PushStatus,
    SubStatus,
    TrackingStatus,
)
from .errors import (
    AuthenticationError,
    QuotaExhaustedError,
    RateLimitError,
    SignatureError,
    Track17APIError,
    Track17ConnectionError,
    Track17Error,
)
from .models import (
    BatchResult,
    CarrierChange,
    InfoChange,
    LatestStatus,
    NumberCarrier,
    Quota,
    RegisteredNumber,
    RejectedItem,
    StoppedNotice,
    TrackEvent,
    TrackInfo,
    TrackListPage,
    TrackRegistration,
)
from .webhook import WebhookEvent, WebhookEventType, parse_event, verify_signature

__all__ = [
    "AuthenticationError",
    "BatchResult",
    "CacheLevel",
    "CarrierChange",
    "ErrorCode",
    "InfoChange",
    "LatestStatus",
    "MainStatus",
    "NumberCarrier",
    "PushStatus",
    "Quota",
    "QuotaExhaustedError",
    "RateLimitError",
    "RegisteredNumber",
    "RejectedItem",
    "SignatureError",
    "StoppedNotice",
    "SubStatus",
    "Track17APIError",
    "Track17Client",
    "Track17ConnectionError",
    "Track17Error",
    "TrackEvent",
    "TrackInfo",
    "TrackListPage",
    "TrackRegistration",
    "TrackingStatus",
    "WebhookEvent",
    "WebhookEventType",
    "parse_event",
    "verify_signature",
]
