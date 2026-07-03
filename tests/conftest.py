"""Shared pytest fixtures for the aio17track test suite."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

type FixtureLoader = Callable[[str], dict[str, Any]]


@pytest.fixture
def load_fixture() -> FixtureLoader:
    """Load a captured API payload from tests/fixtures/<name>.json."""

    def _load(name: str) -> dict[str, Any]:
        payload: dict[str, Any] = json.loads((_FIXTURES_DIR / f"{name}.json").read_text())
        return payload

    return _load
