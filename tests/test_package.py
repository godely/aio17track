"""M0 placeholder: the package and its public surface import cleanly."""

import aio17track


def test_public_surface_imports() -> None:
    assert aio17track.Track17Client is not None
    assert aio17track.verify_signature is not None
    assert aio17track.parse_event is not None
