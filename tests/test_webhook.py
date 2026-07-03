"""Webhook signature verification and event parsing (SPEC §9, §11.4)."""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from aio17track import (
    MainStatus,
    SignatureError,
    StoppedNotice,
    SubStatus,
    Track17APIError,
    TrackInfo,
    WebhookEvent,
    WebhookEventType,
    parse_event,
    verify_signature,
)

type FixtureLoader = Callable[[str], dict[str, Any]]

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_API_KEY = "123456ABCDEF"


def _sign(raw_body: bytes, api_key: str = _API_KEY) -> str:
    return hashlib.sha256(raw_body + b"/" + api_key.encode()).hexdigest()


# --- verify_signature ---


def test_byte_exact_body_verifies() -> None:
    raw = (_FIXTURES_DIR / "webhook_tracking_stopped.json").read_bytes()
    assert verify_signature(raw, _sign(raw), _API_KEY) is True


def test_single_mutated_byte_fails() -> None:
    raw = (_FIXTURES_DIR / "webhook_tracking_stopped.json").read_bytes()
    sign = _sign(raw)
    mutated = bytes([raw[0] ^ 0x01]) + raw[1:]
    with pytest.raises(SignatureError):
        verify_signature(mutated, sign, _API_KEY)


def test_reserialized_json_breaks_the_hash() -> None:
    """The signature covers raw bytes: parsing and re-dumping the same JSON
    with different whitespace must fail verification (SPEC §9 critical note)."""
    raw = b'{"event":"TRACKING_STOPPED","data":{"number":"RR123456789CN","carrier":3011}}'
    sign = _sign(raw)
    redumped = json.dumps(json.loads(raw), indent=2).encode()
    assert redumped != raw
    with pytest.raises(SignatureError):
        verify_signature(redumped, sign, _API_KEY)
    assert verify_signature(raw, sign, _API_KEY) is True


def test_wrong_api_key_fails() -> None:
    raw = (_FIXTURES_DIR / "webhook_tracking_stopped.json").read_bytes()
    with pytest.raises(SignatureError):
        verify_signature(raw, _sign(raw), "another-key")


def test_docs_concatenation_example() -> None:
    """The exact concatenation example from the v2.4 docs."""
    raw = (
        b'{"event":"TRACKING_UPDATED",'
        b'"data":{"number":"RR123456789CN","carrier":3011,"tag":null}}'
    )
    expected = hashlib.sha256(raw + b"/123456ABCDEF").hexdigest()
    assert verify_signature(raw, expected, "123456ABCDEF") is True


# --- parse_event ---


def test_parse_tracking_stopped(load_fixture: FixtureLoader) -> None:
    raw = (_FIXTURES_DIR / "webhook_tracking_stopped.json").read_bytes()
    event = parse_event(raw)

    assert isinstance(event, WebhookEvent)
    assert event.event is WebhookEventType.TRACKING_STOPPED
    assert isinstance(event.data, StoppedNotice)
    assert event.data.number == "AA123456789BR"
    assert event.data.carrier == 2151


def test_parse_tracking_updated(load_fixture: FixtureLoader) -> None:
    item = load_fixture("gettrackinfo_correios_2151")["data"]["accepted"][0]
    raw = json.dumps({"event": "TRACKING_UPDATED", "data": item}).encode()
    event = parse_event(raw)

    assert event.event is WebhookEventType.TRACKING_UPDATED
    assert isinstance(event.data, TrackInfo)
    assert event.data.latest_status.status is MainStatus.DELIVERED
    assert event.data.latest_status.sub_status is SubStatus.DELIVERED_OTHER
    assert len(event.data.events) == 3


@pytest.mark.parametrize(
    "raw",
    [
        b"not json at all",
        b"\xff\xfe garbage bytes",
        b'"just a string"',
        b'{"event": "TRACKING_UPDATED"}',  # no data
        b'{"event": "TRACKING_UPDATED", "data": null}',
        b'{"data": {"number": "X"}}',  # no event
        b'{"event": "SOMETHING_NEW", "data": {"number": "X"}}',  # unknown event
        b'{"event": "TRACKING_STOPPED", "data": {"carrier": 3011}}',  # missing number
        b'{"event": "TRACKING_STOPPED", "data": {"number": "X"}}',  # missing carrier
        b'{"event": "TRACKING_UPDATED", "data": {"carrier": 3011}}',  # missing number
        # nonnumeric carrier must not leak ValueError
        b'{"event": "TRACKING_STOPPED", "data": {"number": "X", "carrier": "not-a-code"}}',
    ],
)
def test_malformed_bodies_raise_api_error(raw: bytes) -> None:
    with pytest.raises(Track17APIError):
        parse_event(raw)


def test_verify_then_parse_roundtrip() -> None:
    raw = (_FIXTURES_DIR / "webhook_tracking_stopped.json").read_bytes()
    assert verify_signature(raw, _sign(raw), _API_KEY)
    event = parse_event(raw)
    assert event.event is WebhookEventType.TRACKING_STOPPED
