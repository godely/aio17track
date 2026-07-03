"""Standalone webhook verification and parsing (SPEC §9). No client required.

Critical: verification runs over the raw request bytes, before any JSON
parse/re-serialize — re-dumping shifts key order or whitespace and breaks
the hash.
"""

from dataclasses import dataclass
from enum import StrEnum

from .models import StoppedNotice, TrackInfo


class WebhookEventType(StrEnum):
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
    """Verify a webhook signature over the raw request bytes.

    signed string = raw_body_text + "/" + api_key;
    expected = sha256(signed_string).hexdigest();
    compared against ``sign_header`` with ``hmac.compare_digest``
    (constant-time). Signature failure raises ``SignatureError``.
    """
    raise NotImplementedError


def parse_event(raw_body: bytes) -> WebhookEvent:
    """Parse a webhook body into a ``WebhookEvent``.

    Malformed body raises ``Track17APIError``.
    """
    raise NotImplementedError
