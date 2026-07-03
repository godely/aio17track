"""Status, push, cache-level, and error-code enums for the 17TRACK v2.4 API (SPEC §8).

Member tables were pulled from the official v2.4 docs
(https://api.17track.net/en/doc?version=v2.4, captured 2026-07-03): 9 main
statuses, 30 sub-statuses, and the full error-code table.

Forward-compatibility rule (non-negotiable): every enum parsed from an API
payload falls back to UNKNOWN instead of raising. Lookups of unrecognized
values return a pseudo-member named ``UNKNOWN`` whose ``value`` is the raw
string/int the API sent, so nothing is lost. Match on ``member.name`` or use
``is_unknown``; identity with the declared UNKNOWN member is not guaranteed
for pseudo-members.
"""

from enum import Enum, IntEnum, StrEnum
from typing import Self


class _StrEnumWithUnknown(StrEnum):
    """StrEnum whose lookup never raises on unrecognized values."""

    @classmethod
    def _missing_(cls, value: object) -> Self:
        if not isinstance(value, str):
            return cls["UNKNOWN"]
        # Pseudo-member: named UNKNOWN, carrying the raw string as its value.
        member = str.__new__(cls, value)
        member._name_ = "UNKNOWN"
        member._value_ = value
        return member

    @property
    def is_unknown(self) -> bool:
        return self._name_ == "UNKNOWN"


class _IntEnumWithUnknown(IntEnum):
    """IntEnum whose lookup never raises on unrecognized values."""

    @classmethod
    def _missing_(cls, value: object) -> Self:
        if not isinstance(value, int):
            return cls["UNKNOWN"]
        # Pseudo-member: named UNKNOWN, carrying the raw int as its value.
        member = int.__new__(cls, value)
        member._name_ = "UNKNOWN"
        member._value_ = value
        return member

    @property
    def is_unknown(self) -> bool:
        return self._name_ == "UNKNOWN"


class MainStatus(_StrEnumWithUnknown):
    """Package main status (``latest_status.status``) — 9 documented values."""

    UNKNOWN = "UNKNOWN"
    NOT_FOUND = "NotFound"
    INFO_RECEIVED = "InfoReceived"
    IN_TRANSIT = "InTransit"
    EXPIRED = "Expired"
    AVAILABLE_FOR_PICKUP = "AvailableForPickup"
    OUT_FOR_DELIVERY = "OutForDelivery"
    DELIVERY_FAILURE = "DeliveryFailure"
    DELIVERED = "Delivered"
    EXCEPTION = "Exception"


class SubStatus(_StrEnumWithUnknown):
    """Package sub-status (``latest_status.sub_status``) — 30 documented values."""

    UNKNOWN = "UNKNOWN"
    # NotFound
    NOT_FOUND_OTHER = "NotFound_Other"
    NOT_FOUND_INVALID_CODE = "NotFound_InvalidCode"
    # InfoReceived (no breakdown; same as the main status)
    INFO_RECEIVED = "InfoReceived"
    # InTransit
    IN_TRANSIT_PICKED_UP = "InTransit_PickedUp"
    IN_TRANSIT_OTHER = "InTransit_Other"
    IN_TRANSIT_DEPARTURE = "InTransit_Departure"
    IN_TRANSIT_ARRIVAL = "InTransit_Arrival"
    IN_TRANSIT_CUSTOMS_PROCESSING = "InTransit_CustomsProcessing"
    IN_TRANSIT_CUSTOMS_RELEASED = "InTransit_CustomsReleased"
    IN_TRANSIT_CUSTOMS_REQUIRING_INFORMATION = "InTransit_CustomsRequiringInformation"
    # Expired
    EXPIRED_OTHER = "Expired_Other"
    # AvailableForPickup
    AVAILABLE_FOR_PICKUP_OTHER = "AvailableForPickup_Other"
    # OutForDelivery
    OUT_FOR_DELIVERY_OTHER = "OutForDelivery_Other"
    # DeliveryFailure
    DELIVERY_FAILURE_OTHER = "DeliveryFailure_Other"
    DELIVERY_FAILURE_NO_BODY = "DeliveryFailure_NoBody"
    DELIVERY_FAILURE_SECURITY = "DeliveryFailure_Security"
    DELIVERY_FAILURE_REJECTED = "DeliveryFailure_Rejected"
    DELIVERY_FAILURE_INVALID_ADDRESS = "DeliveryFailure_InvalidAddress"
    # Delivered (note: for Delivered_Other, the true delivery time lives in
    # the event's time_raw — see TrackEvent)
    DELIVERED_OTHER = "Delivered_Other"
    # Exception
    EXCEPTION_OTHER = "Exception_Other"
    EXCEPTION_RETURNING = "Exception_Returning"
    EXCEPTION_RETURNED = "Exception_Returned"
    EXCEPTION_NO_BODY = "Exception_NoBody"
    EXCEPTION_SECURITY = "Exception_Security"
    EXCEPTION_DAMAGE = "Exception_Damage"
    EXCEPTION_REJECTED = "Exception_Rejected"
    EXCEPTION_DELAYED = "Exception_Delayed"
    EXCEPTION_LOST = "Exception_Lost"
    EXCEPTION_DESTROYED = "Exception_Destroyed"
    EXCEPTION_CANCEL = "Exception_Cancel"


class TrackingStatus(_StrEnumWithUnknown):
    """Whether 17track is still tracking the number (``tracking_status``).

    Exactly two documented values; the stop reason lives in the separate
    ``stop_track_reason`` field ("Expired" / "ByRequest" / "InvalidCarrier").
    """

    UNKNOWN = "UNKNOWN"
    TRACKING = "Tracking"
    STOPPED = "Stopped"


class PushStatus(_StrEnumWithUnknown):
    """Webhook delivery outcome (``push_status``)."""

    UNKNOWN = "UNKNOWN"
    NOT_PUSHED = "NotPushed"
    SUCCESS = "Success"
    FAILURE = "Failure"


class CacheLevel(Enum):
    """Cache level for ``Track17Client.get_realtime_track_info``.

    Warning: INSTANT deducts 10 credits per call (STANDARD deducts 1).
    INSTANT must never be the default anywhere.
    """

    # TODO(M6): the v2.4 docs contradict themselves on the `cacheLevel` wire
    # value — prose says the strings "Standard"/"Instant", the parameter
    # table says Int 0/1. Resolve against the live API when wiring realtime.
    UNKNOWN = "UNKNOWN"
    STANDARD = "Standard"
    INSTANT = "Instant"


class ErrorCode(_IntEnumWithUnknown):
    """The full documented 17track error-code table (SPEC §7).

    Codes appear request-level (top-level ``code`` / ``data.errors[]``) or
    per-item (``rejected[].error.code``); the docs do not annotate placement
    per code, so no distinction is encoded here. Unrecognized codes map to a
    pseudo-member named UNKNOWN carrying the raw int.
    """

    UNKNOWN = -1  # sentinel only; unrecognized codes carry their raw value
    SUCCESS = 0
    # Account / request validity (-180100xx)
    IP_NOT_WHITELISTED = -18010001
    INVALID_SECURITY_KEY = -18010002
    INTERNAL_SERVICE_ERROR = -18010003
    ACCOUNT_DISABLED = -18010004
    UNAUTHORIZED_ACCESS = -18010005
    MISSING_REQUIRED_DATA = -18010010
    INVALID_DATA_VALUE = -18010011
    INVALID_DATA_FORMAT = -18010012
    INVALID_SUBMITTED_DATA = -18010013
    EXCEEDS_NUMBER_LIMIT = -18010014
    INVALID_FIELD_VALUE = -18010015
    LAST_MILE_ONLY_FOR_POSTAL = -18010016
    COUNTRY_POSTAL_CODE_REQUIRED = -18010018
    POSTAL_CODE_REQUIRED = -18010019
    PHONE_NUMBER_REQUIRED = -18010020
    ADDITIONAL_INFO_REQUIRED = -18010022
    # Webhook configuration (-180102xx)
    WEBHOOK_URL_REQUIRED = -18010201
    WEBHOOK_URL_INVALID = -18010202
    WEBHOOK_TEST_FAILED = -18010203
    WEBHOOK_URL_NOT_SET = -18010204
    INVALID_IP_FORMAT = -18010205
    PUSH_FAILED = -18010206
    # Carrier change (-180198xx)
    CARRIER_OLD_REQUIRED = -18019801
    CARRIER_NEW_INCORRECT = -18019802
    CARRIER_CHANGE_SAME_CODE = -18019803
    CARRIER_NEW_REQUIRED = -18019804
    CARRIER_OLD_MISMATCH = -18019805
    CARRIER_CHANGE_ON_STOPPED = -18019806
    CARRIER_CHANGE_LIMIT_EXCEEDED = -18019807
    CARRIER_CHANGE_PENDING_RESULT = -18019808
    CARRIER_CHANGE_DUPLICATE = -18019809
    UPDATE_NOT_UNIQUE = -18019810
    UPDATE_DATA_INVALID = -18019811
    # Tracking lifecycle (-180199xx)
    ALREADY_REGISTERED = -18019901
    NOT_REGISTERED = -18019902
    CARRIER_NOT_DETECTED = -18019903
    RETRACK_ONLY_STOPPED = -18019904
    RETRACK_ONLY_ONCE = -18019905
    STOP_ONLY_TRACKING = -18019906
    DAILY_LIMIT_EXCEEDED = -18019907
    QUOTA_EXHAUSTED = -18019908
    NO_TRACKING_INFO_YET = -18019909
    INCORRECT_CARRIER_CODE = -18019910
    CARRIER_NOT_REGISTRABLE = -18019911
    REALTIME_UNAUTHORIZED = -18019912
    # Realtime carrier interface (-1801981x)
    REALTIME_CARRIER_TIMEOUT = -18019815
    REALTIME_CARRIER_ERROR = -18019816
    REALTIME_CHARGE_NOT_PROCESSED = -18019817
    REALTIME_CARRIER_UNSUPPORTED = -18019818
