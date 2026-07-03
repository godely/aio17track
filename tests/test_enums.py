"""Enum forward-compatibility: unknown values never raise (SPEC §8, §11.2).

Property-style tests using seeded stdlib randomness (hypothesis is not a
dev dependency by design).
"""

import random
import string

import pytest

from aio17track import (
    CacheLevel,
    ErrorCode,
    MainStatus,
    PushStatus,
    SubStatus,
    TrackingStatus,
)
from aio17track.enums import _StrEnumWithUnknown

STR_ENUMS: list[type[_StrEnumWithUnknown]] = [
    MainStatus,
    SubStatus,
    TrackingStatus,
    PushStatus,
]

_ALPHABET = string.ascii_letters + string.digits + "_- ./é中"


def _random_strings(seed: int, count: int) -> list[str]:
    rng = random.Random(seed)
    return [
        "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 40))) for _ in range(count)
    ]


@pytest.mark.parametrize("enum_cls", STR_ENUMS)
def test_unknown_strings_never_raise_and_carry_raw(
    enum_cls: type[_StrEnumWithUnknown],
) -> None:
    known_values = {member.value for member in enum_cls}
    for raw in _random_strings(seed=17, count=500):
        member = enum_cls(raw)
        if raw in known_values:
            continue  # astronomically unlikely, but a real member is fine too
        assert member.name == "UNKNOWN"
        assert member.is_unknown
        assert member.value == raw  # raw string preserved


@pytest.mark.parametrize("enum_cls", STR_ENUMS)
def test_known_values_resolve_to_declared_members(
    enum_cls: type[_StrEnumWithUnknown],
) -> None:
    for member in enum_cls:
        resolved = enum_cls(member.value)
        assert resolved is member


@pytest.mark.parametrize("enum_cls", STR_ENUMS)
def test_non_string_lookup_falls_back_to_unknown(
    enum_cls: type[_StrEnumWithUnknown],
) -> None:
    for value in (None, 3, 4.2, object()):
        member = enum_cls(value)  # type: ignore[arg-type]
        assert member.is_unknown


def test_declared_unknown_member_is_unknown() -> None:
    assert MainStatus.UNKNOWN.is_unknown
    assert not MainStatus.DELIVERED.is_unknown


def test_unknown_error_codes_never_raise_and_carry_raw() -> None:
    rng = random.Random(17)
    known_values = {member.value for member in ErrorCode}
    for _ in range(500):
        raw = rng.randint(-99_999_999, 99_999_999)
        code = ErrorCode(raw)
        if raw in known_values:
            continue
        assert code.name == "UNKNOWN"
        assert code.is_unknown
        assert code.value == raw  # raw int preserved
        assert int(code) == raw


def test_known_error_codes_resolve_to_declared_members() -> None:
    assert ErrorCode(-18019901) is ErrorCode.ALREADY_REGISTERED
    assert ErrorCode(-18019901).name == "ALREADY_REGISTERED"


def test_non_int_error_code_falls_back_to_unknown() -> None:
    assert ErrorCode("not-a-code").is_unknown  # type: ignore[arg-type]


def test_main_status_covers_the_nine_documented_values() -> None:
    declared = {member.value for member in MainStatus} - {"UNKNOWN"}
    assert declared == {
        "NotFound",
        "InfoReceived",
        "InTransit",
        "Expired",
        "AvailableForPickup",
        "OutForDelivery",
        "DeliveryFailure",
        "Delivered",
        "Exception",
    }


def test_sub_status_covers_the_thirty_documented_values() -> None:
    assert len([member for member in SubStatus if member is not SubStatus.UNKNOWN]) == 30


def test_error_code_table_spot_checks() -> None:
    assert ErrorCode(0) is ErrorCode.SUCCESS
    assert ErrorCode(-18010002) is ErrorCode.INVALID_SECURITY_KEY
    assert ErrorCode(-18019902) is ErrorCode.NOT_REGISTERED
    assert ErrorCode(-18019908) is ErrorCode.QUOTA_EXHAUSTED
    assert ErrorCode(-18019818) is ErrorCode.REALTIME_CARRIER_UNSUPPORTED


def test_tracking_status_has_exactly_two_documented_values() -> None:
    assert {member.value for member in TrackingStatus} - {"UNKNOWN"} == {"Tracking", "Stopped"}


def test_push_status_covers_documented_values() -> None:
    declared = {member.value for member in PushStatus} - {"UNKNOWN"}
    assert declared == {"NotPushed", "Success", "Failure"}


def test_cache_level_instant_is_not_default_anywhere() -> None:
    import inspect

    from aio17track import Track17Client

    signature = inspect.signature(Track17Client.get_realtime_track_info)
    assert signature.parameters["cache_level"].default is CacheLevel.STANDARD
