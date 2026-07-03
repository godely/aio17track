"""from_api field mapping against v2.4-shaped fixtures (SPEC §6, §11.1)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from aio17track import (
    ErrorCode,
    LatestStatus,
    MainStatus,
    Quota,
    RegisteredNumber,
    RejectedItem,
    StoppedNotice,
    SubStatus,
    Track17APIError,
    TrackEvent,
    TrackInfo,
    TrackingStatus,
    TrackListPage,
)

type FixtureLoader = Callable[[str], dict[str, Any]]

# --- TrackInfo / TrackEvent / LatestStatus ---


def test_track_info_correios_delivered(load_fixture: FixtureLoader) -> None:
    item = load_fixture("gettrackinfo_correios_2151")["data"]["accepted"][0]
    info = TrackInfo.from_api(item)

    assert info.number == "AA123456789BR"
    assert info.carrier == 2151
    assert info.latest_status == LatestStatus(
        status=MainStatus.DELIVERED, sub_status=SubStatus.DELIVERED_OTHER
    )
    assert info.shipping_country == "BR"
    assert info.recipient_country == "BR"
    assert not info.stopped
    assert len(info.events) == 3


def test_delivered_other_delivery_time_survives_in_time_raw(
    load_fixture: FixtureLoader,
) -> None:
    """SPEC §6 timestamp rule: for Delivered_Other the true delivery time is
    in time_raw; time_iso was null on the wire so it must stay None (no
    fabricated timezone, no fabricated timestamp)."""
    item = load_fixture("gettrackinfo_correios_2151")["data"]["accepted"][0]
    latest = TrackInfo.from_api(item).latest_event

    assert latest is not None
    assert latest.sub_status is SubStatus.DELIVERED_OTHER
    assert latest.time_iso is None
    assert latest.time_raw == "2026-06-30 14:23:00"


def test_track_event_parses_aware_datetime_and_coordinates(
    load_fixture: FixtureLoader,
) -> None:
    item = load_fixture("gettrackinfo_correios_2151")["data"]["accepted"][0]
    out_for_delivery = TrackInfo.from_api(item).events[1]

    assert out_for_delivery.time_iso == datetime(
        2026, 6, 28, 8, 40, tzinfo=timezone(timedelta(hours=-3))
    )
    assert out_for_delivery.time_iso.tzinfo is not None  # aware, from the wire offset
    assert out_for_delivery.time_raw == "2026-06-28 08:40:00 -03:00"
    assert out_for_delivery.coordinates == (-8.0539, -34.877)  # (lat, lon)
    assert out_for_delivery.stage == "OutForDelivery"
    assert out_for_delivery.description == "Objeto saiu para entrega ao destinatário"
    assert out_for_delivery.location == "RECIFE, PE"


def test_track_event_without_coordinates_maps_to_none(load_fixture: FixtureLoader) -> None:
    item = load_fixture("gettrackinfo_correios_2151")["data"]["accepted"][0]
    delivered = TrackInfo.from_api(item).events[0]
    assert delivered.coordinates is None


def test_track_info_yanwen_in_transit(load_fixture: FixtureLoader) -> None:
    item = load_fixture("gettrackinfo_yanwen_190012")["data"]["accepted"][0]
    info = TrackInfo.from_api(item)

    assert info.number == "YT2616912345678901"
    assert info.carrier == 190012
    assert info.latest_status.status is MainStatus.IN_TRANSIT
    assert info.latest_status.sub_status is SubStatus.IN_TRANSIT_ARRIVAL
    assert info.shipping_country == "CN"
    assert info.recipient_country == "US"
    assert info.latest_event is not None
    assert info.latest_event.time_iso == datetime(
        2026, 6, 29, 21, 5, tzinfo=timezone(timedelta(hours=8))
    )


def test_unknown_sub_status_in_events_never_raises(load_fixture: FixtureLoader) -> None:
    """The incumbent library crashes on new statuses; that is the defect we
    must not reproduce (SPEC §8). The middle YanWen event carries a made-up
    future sub-status."""
    item = load_fixture("gettrackinfo_yanwen_190012")["data"]["accepted"][0]
    future_event = TrackInfo.from_api(item).events[1]

    assert future_event.sub_status.is_unknown
    assert future_event.sub_status.name == "UNKNOWN"
    assert future_event.sub_status.value == "InTransit_QuantumTunnelling"  # raw preserved


def test_track_info_exception_returning(load_fixture: FixtureLoader) -> None:
    item = load_fixture("gettrackinfo_exception_returning")["data"]["accepted"][0]
    info = TrackInfo.from_api(item)

    assert info.latest_status.status is MainStatus.EXCEPTION
    assert info.latest_status.sub_status is SubStatus.EXCEPTION_RETURNING
    assert info.events[0].stage == "Returning"


def test_unmodeled_track_info_sections_land_in_extra(load_fixture: FixtureLoader) -> None:
    item = load_fixture("gettrackinfo_correios_2151")["data"]["accepted"][0]
    extra = TrackInfo.from_api(item).tracking_number_extra

    assert set(extra) == {"time_metrics", "milestone", "misc_info"}
    assert extra["misc_info"]["service_type"] == "SEDEX"


# --- RejectedItem ---


def test_rejected_item_from_register(load_fixture: FixtureLoader) -> None:
    raw = load_fixture("register_mixed")["data"]["rejected"][0]
    item = RejectedItem.from_api(raw)

    assert item.number == "1234"
    assert item.carrier == 0
    assert item.error_code is ErrorCode.INVALID_DATA_FORMAT
    assert item.error_message == "The format of '1234' is invalid."


def test_rejected_item_coerces_string_carrier(load_fixture: FixtureLoader) -> None:
    """gettrackinfo's documented example sends carrier as the string "3011"."""
    raw = load_fixture("gettrackinfo_yanwen_190012")["data"]["rejected"][0]
    item = RejectedItem.from_api(raw)

    assert item.carrier == 3011
    assert item.error_code is ErrorCode.NOT_REGISTERED


def test_rejected_item_already_registered(load_fixture: FixtureLoader) -> None:
    raw = load_fixture("register_already_registered")["data"]["rejected"][0]
    item = RejectedItem.from_api(raw)

    assert item.error_code is ErrorCode.ALREADY_REGISTERED
    assert item.number == "AA123456789BR"


# --- RegisteredNumber ---


def test_registered_number_from_track_list_item(load_fixture: FixtureLoader) -> None:
    raw = load_fixture("gettracklist_page")["data"]["accepted"][0]
    number = RegisteredNumber.from_api(raw)

    assert number.number == "AA123456789BR"
    assert number.carrier == 2151
    assert number.tracking_status is TrackingStatus.TRACKING
    assert number.package_status is MainStatus.DELIVERED
    assert number.register_time == datetime(2026, 6, 24, 18, 2, 11, tzinfo=UTC)
    assert number.tag == "order-1042"
    assert number.order_no == "86574382938"


def test_registered_number_stopped_expired(load_fixture: FixtureLoader) -> None:
    raw = load_fixture("gettracklist_page")["data"]["accepted"][1]
    number = RegisteredNumber.from_api(raw)

    assert number.tracking_status is TrackingStatus.STOPPED
    assert number.package_status is MainStatus.EXPIRED
    assert number.tag is None
    assert number.order_no is None


def test_registered_number_from_register_accepted_item(load_fixture: FixtureLoader) -> None:
    """register accepted items carry no status fields — they land as UNKNOWN."""
    raw = load_fixture("register_mixed")["data"]["accepted"][0]
    number = RegisteredNumber.from_api(raw)

    assert number.number == "AA123456789BR"
    assert number.carrier == 2151
    assert number.tracking_status is TrackingStatus.UNKNOWN
    assert number.package_status is MainStatus.UNKNOWN
    assert number.register_time is None
    assert number.tag == "order-1042"


# --- TrackListPage ---


def test_track_list_page_from_envelope(load_fixture: FixtureLoader) -> None:
    page = TrackListPage.from_api(load_fixture("gettracklist_page"))

    assert page.page_no == 1
    assert page.page_total == 2
    assert page.data_total == 43
    assert len(page.items) == 2
    assert page.items[0].number == "AA123456789BR"


def test_track_list_page_rejects_data_only_payload(load_fixture: FixtureLoader) -> None:
    """A data-only dict has already lost the sibling `page` object; silently
    defaulting page_total to 1 would hide further pages from callers."""
    envelope = load_fixture("gettracklist_page")

    with pytest.raises(Track17APIError, match="page"):
        TrackListPage.from_api(envelope["data"])


# --- Quota ---


def test_quota_field_mapping(load_fixture: FixtureLoader) -> None:
    quota = Quota.from_api(load_fixture("getquota")["data"])

    assert quota.remaining == 1098
    assert quota.used == 2
    assert quota.total == 1100


# --- StoppedNotice / TrackEvent standalone ---


def test_stopped_notice_from_webhook_payload(load_fixture: FixtureLoader) -> None:
    notice = StoppedNotice.from_api(load_fixture("webhook_tracking_stopped")["data"])

    assert notice.number == "AA123456789BR"
    assert notice.carrier == 2151
    assert notice.param is None
    assert notice.tag is None  # empty string on the wire normalizes to None


def test_track_event_from_empty_dict_is_all_none() -> None:
    event = TrackEvent.from_api({})

    assert event.time_iso is None
    assert event.time_raw is None
    assert event.description is None
    assert event.location is None
    assert event.stage is None
    assert event.sub_status is SubStatus.UNKNOWN
    assert event.coordinates is None


def test_naive_datetime_is_not_given_a_timezone() -> None:
    """Never fabricate a timezone the API did not send (SPEC §6)."""
    event = TrackEvent.from_api({"time_iso": "2026-06-30T14:23:00"})

    assert event.time_iso == datetime(2026, 6, 30, 14, 23)
    assert event.time_iso.tzinfo is None


# --- malformed wire data never raises ---


def test_malformed_timestamps_and_coordinates_map_to_none() -> None:
    event = TrackEvent.from_api(
        {
            "time_iso": "not-a-date",
            "time_raw": {"date": None, "time": None, "timezone": None},
            "address": {"coordinates": {"longitude": "east-ish", "latitude": "12.5"}},
        }
    )

    assert event.time_iso is None
    assert event.time_raw is None
    assert event.coordinates is None


def test_time_raw_as_plain_string_is_preserved() -> None:
    event = TrackEvent.from_api({"time_raw": "2026-06-30 14:23"})
    assert event.time_raw == "2026-06-30 14:23"


def test_rejected_item_with_unparseable_carrier_maps_to_none() -> None:
    item = RejectedItem.from_api(
        {"number": "X1", "carrier": "not-a-code", "error": {"code": -18019902, "message": "m"}}
    )
    assert item.carrier is None


def test_rejected_item_with_missing_error_object_is_unknown() -> None:
    item = RejectedItem.from_api({"number": "X1"})

    assert item.error_code.is_unknown
    assert item.error_message == ""
    assert item.carrier is None
