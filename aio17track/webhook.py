"""Standalone webhook verification and parsing (SPEC §9). No client required.

Critical: verification runs over the raw request bytes, before any JSON
parse/re-serialize — re-dumping shifts key order or whitespace and breaks
the hash.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass

from .enums import _StrEnumWithUnknown
from .errors import SignatureError, Track17APIError
from .models import StoppedNotice, TrackInfo


class WebhookEventType(_StrEnumWithUnknown):
    """Kind of webhook event pushed by 17track."""

    UNKNOWN = "UNKNOWN"
    TRACKING_UPDATED = "TRACKING_UPDATED"
    TRACKING_STOPPED = "TRACKING_STOPPED"


@dataclass(slots=True, frozen=True)
class WebhookEvent:
    """A parsed webhook push."""

    event: WebhookEventType
    data: TrackInfo | StoppedNotice


def verify_signature(raw_body: bytes, sign_header: str, api_key: str) -> bool:
    """Verify the ``sign`` header over the raw request bytes.

    The signed string is ``<raw body>/<api key>``, hashed with SHA-256 and
    hex-encoded, compared in constant time. Returns True on a match; a
    mismatch raises ``SignatureError`` (SPEC §9).

    Hash the bytes exactly as received — parsing and re-serializing the
    JSON shifts key order or whitespace and breaks the hash.
    """
    signed = raw_body + b"/" + api_key.encode("utf-8")
    expected = hashlib.sha256(signed).hexdigest()
    if not hmac.compare_digest(expected, sign_header):
        raise SignatureError("webhook signature mismatch")
    return True


def parse_event(raw_body: bytes) -> WebhookEvent:
    """Parse a webhook body into a ``WebhookEvent``.

    ``TRACKING_UPDATED`` data parses as ``TrackInfo`` (same shape as a
    gettrackinfo accepted item); ``TRACKING_STOPPED`` as ``StoppedNotice``.
    A malformed body — invalid JSON, missing ``data``, missing required
    fields, or an event type we cannot shape-map — raises
    ``Track17APIError``.
    """
    try:
        payload = json.loads(raw_body)
    except ValueError as exc:  # includes UnicodeDecodeError / JSONDecodeError
        raise Track17APIError(-1, "webhook body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise Track17APIError(-1, "webhook body is not a JSON object")

    event_raw = payload.get("event")
    event = WebhookEventType(event_raw) if isinstance(event_raw, str) else WebhookEventType.UNKNOWN
    data = payload.get("data")
    if not isinstance(data, dict):
        raise Track17APIError(-1, "webhook body has no 'data' object")

    if event in (WebhookEventType.TRACKING_UPDATED, WebhookEventType.TRACKING_STOPPED):
        # The documented payloads always carry both identifiers; the API
        # models are deliberately permissive, so enforce presence here rather
        # than let a missing carrier default to 0 or a bad one leak ValueError.
        for field in ("number", "carrier"):
            if data.get(field) is None:
                raise Track17APIError(
                    -1, f"webhook data is missing required field {field!r}"
                )
        try:
            if event is WebhookEventType.TRACKING_UPDATED:
                return WebhookEvent(event=event, data=TrackInfo.from_api(data))
            return WebhookEvent(event=event, data=StoppedNotice.from_api(data))
        except (KeyError, TypeError, ValueError) as exc:
            raise Track17APIError(-1, f"webhook data is malformed: {exc}") from exc
    # The enum lookup never raises (forward-compat rule); an event we do not
    # know still fails here because its data shape is unknowable.
    raise Track17APIError(-1, f"unrecognized webhook event {payload.get('event')!r}")
