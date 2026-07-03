"""Error taxonomy: hierarchy shape and data-carrying constructors (SPEC §7)."""

import pytest

from aio17track import (
    AuthenticationError,
    QuotaExhaustedError,
    RateLimitError,
    SignatureError,
    Track17APIError,
    Track17ConnectionError,
    Track17Error,
)


@pytest.mark.parametrize(
    "exc_type",
    [
        Track17ConnectionError,
        AuthenticationError,
        RateLimitError,
        QuotaExhaustedError,
        SignatureError,
        Track17APIError,
    ],
)
def test_all_errors_derive_from_base(exc_type: type[Track17Error]) -> None:
    assert issubclass(exc_type, Track17Error)
    assert issubclass(exc_type, Exception)


def test_rate_limit_error_carries_retry_after() -> None:
    err = RateLimitError("throttled", retry_after=1.5)
    assert err.retry_after == 1.5
    assert str(err) == "throttled"


def test_rate_limit_error_retry_after_defaults_to_none() -> None:
    assert RateLimitError("throttled").retry_after is None


def test_api_error_carries_code_and_message() -> None:
    err = Track17APIError(-18010012, "something went wrong")
    assert err.code == -18010012
    assert err.message == "something went wrong"
    assert str(err) == "something went wrong"


def test_errors_are_catchable_as_base() -> None:
    with pytest.raises(Track17Error):
        raise Track17APIError(-1, "boom")
