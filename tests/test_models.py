"""Model behavior that does not require API fixtures: BatchResult views."""

from aio17track import BatchResult, ErrorCode, RegisteredNumber, RejectedItem


def _rejected(number: str, code: ErrorCode) -> RejectedItem:
    return RejectedItem(number=number, carrier=None, error_code=code, error_message="msg")


def test_ok_is_true_without_rejections() -> None:
    result: BatchResult[RegisteredNumber] = BatchResult(accepted=(), rejected=())
    assert result.ok


def test_ok_is_false_with_rejections() -> None:
    result: BatchResult[RegisteredNumber] = BatchResult(
        accepted=(), rejected=(_rejected("A1", ErrorCode.INVALID_DATA_FORMAT),)
    )
    assert not result.ok


def test_already_registered_filters_only_that_code() -> None:
    dup1 = _rejected("A1", ErrorCode.ALREADY_REGISTERED)
    dup2 = _rejected("A2", ErrorCode.ALREADY_REGISTERED)
    other = _rejected("A3", ErrorCode.INVALID_DATA_FORMAT)
    unknown = _rejected("A4", ErrorCode(-12345678))
    result: BatchResult[RegisteredNumber] = BatchResult(
        accepted=(), rejected=(dup1, other, dup2, unknown)
    )
    assert result.already_registered == (dup1, dup2)


def test_already_registered_empty_when_no_rejections() -> None:
    result: BatchResult[RegisteredNumber] = BatchResult(accepted=(), rejected=())
    assert result.already_registered == ()
