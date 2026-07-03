"""Exception taxonomy — request-level failures (SPEC §7, plane 1).

Item-level rejections never raise; they arrive typed as ``RejectedItem``
inside ``BatchResult.rejected`` (plane 2).
"""


class Track17Error(Exception):
    """Base class for every error raised by aio17track."""


class Track17ConnectionError(Track17Error):
    """Network failure or timeout (wraps ``aiohttp.ClientError``)."""


class AuthenticationError(Track17Error):
    """HTTP 401, or codes -18010002, -18010001 (IP whitelist), -18010004 (disabled)."""


class RateLimitError(Track17Error):
    """HTTP 429 after retries were exhausted."""

    retry_after: float | None

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class QuotaExhaustedError(Track17Error):
    """Codes -18019907 / -18019908."""


class SignatureError(Track17Error):
    """Webhook signature verification failed (raised by ``webhook.py``)."""


class Track17APIError(Track17Error):
    """Any other non-zero request-level code."""

    code: int
    message: str

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
