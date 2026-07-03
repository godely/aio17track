"""CarrierCatalog: lazy fetch, in-memory + on-disk cache, lookups (SPEC §10)."""

import json
from pathlib import Path

import aiohttp
import pytest
from aioresponses import aioresponses

from aio17track import Track17APIError, Track17ConnectionError
from aio17track.carriers import _CARRIER_LIST_URL, CarrierCatalog

_SAMPLE = [
    {"key": 2151, "_country": "BR", "_name": "Correios"},
    {"key": 190012, "_country": "CN", "_name": "Yanwen"},
    {"key": 100003, "_country": "US", "_name": "FedEx"},
    {"key": "not-an-int", "_name": "Bogus"},  # skipped: bad key
    {"_name": "No key"},  # skipped: missing key
    "junk entry",  # skipped: not an object
]


async def _loaded_catalog(*, cache_path: Path | None = None) -> CarrierCatalog:
    catalog = CarrierCatalog(cache_path=cache_path)
    async with aiohttp.ClientSession() as session:
        with aioresponses() as mocked:
            mocked.get(_CARRIER_LIST_URL, payload=_SAMPLE)
            await catalog.load(session)
    return catalog


async def test_lookups_after_load() -> None:
    catalog = await _loaded_catalog()
    assert catalog.name(2151) == "Correios"
    assert catalog.name(190012) == "Yanwen"
    assert catalog.name(999999) is None
    assert catalog.code("Correios") == 2151
    assert catalog.code("fedex") == 100003  # case-insensitive
    assert catalog.code("Unknown Carrier") is None


async def test_all_returns_readonly_mapping() -> None:
    catalog = await _loaded_catalog()
    mapping = catalog.all()
    assert dict(mapping) == {2151: "Correios", 190012: "Yanwen", 100003: "FedEx"}
    with pytest.raises(TypeError):
        mapping[1] = "nope"  # type: ignore[index]


def test_lookup_before_load_raises() -> None:
    catalog = CarrierCatalog()
    with pytest.raises(RuntimeError, match="load"):
        catalog.name(2151)
    with pytest.raises(RuntimeError, match="load"):
        catalog.code("Correios")
    with pytest.raises(RuntimeError, match="load"):
        catalog.all()


async def test_second_load_is_a_noop() -> None:
    catalog = CarrierCatalog()
    async with aiohttp.ClientSession() as session:
        with aioresponses() as mocked:
            mocked.get(_CARRIER_LIST_URL, payload=_SAMPLE)  # single response only
            await catalog.load(session)
            await catalog.load(session)  # a second fetch would blow up the mock
    assert catalog.name(2151) == "Correios"


async def test_disk_cache_written_and_reused(tmp_path: Path) -> None:
    cache_file = tmp_path / "carriers.json"
    await _loaded_catalog(cache_path=cache_file)
    assert json.loads(cache_file.read_text()) == _SAMPLE

    # A fresh catalog with the same path must not hit the network at all:
    # no response is registered, so any request would fail loudly.
    fresh = CarrierCatalog(cache_path=cache_file)
    async with aiohttp.ClientSession() as session:
        with aioresponses():
            await fresh.load(session)
    assert fresh.name(190012) == "Yanwen"


async def test_invalid_payload_is_never_persisted(tmp_path: Path) -> None:
    """A 200 with valid JSON that is not the carrier array must not poison
    the on-disk cache: load fails loudly and no cache file is written."""
    cache_file = tmp_path / "carriers.json"
    catalog = CarrierCatalog(cache_path=cache_file)
    async with aiohttp.ClientSession() as session:
        with aioresponses() as mocked:
            mocked.get(_CARRIER_LIST_URL, payload={"error": "maintenance"})
            with pytest.raises(Track17APIError, match="array"):
                await catalog.load(session)
    assert not cache_file.exists()


async def test_shape_poisoned_cache_recovers_via_refetch(tmp_path: Path) -> None:
    """Valid-JSON-but-wrong-shape cache content triggers a refetch and gets
    repaired instead of being reused forever."""
    cache_file = tmp_path / "carriers.json"
    cache_file.write_text('{"error": "maintenance"}')
    catalog = CarrierCatalog(cache_path=cache_file)
    async with aiohttp.ClientSession() as session:
        with aioresponses() as mocked:
            mocked.get(_CARRIER_LIST_URL, payload=_SAMPLE)
            await catalog.load(session)
    assert catalog.name(2151) == "Correios"
    assert json.loads(cache_file.read_text()) == _SAMPLE  # cache repaired


async def test_corrupt_disk_cache_falls_back_to_fetch(tmp_path: Path) -> None:
    cache_file = tmp_path / "carriers.json"
    cache_file.write_text("{ not json")
    catalog = CarrierCatalog(cache_path=cache_file)
    async with aiohttp.ClientSession() as session:
        with aioresponses() as mocked:
            mocked.get(_CARRIER_LIST_URL, payload=_SAMPLE)
            await catalog.load(session)
    assert catalog.name(2151) == "Correios"
    assert json.loads(cache_file.read_text()) == _SAMPLE  # cache repaired


async def test_http_error_raises_api_error() -> None:
    catalog = CarrierCatalog()
    async with aiohttp.ClientSession() as session:
        with aioresponses() as mocked:
            mocked.get(_CARRIER_LIST_URL, status=503)
            with pytest.raises(Track17APIError) as excinfo:
                await catalog.load(session)
    assert excinfo.value.code == 503


async def test_connection_error_raises_connection_error() -> None:
    catalog = CarrierCatalog()
    async with aiohttp.ClientSession() as session:
        with aioresponses() as mocked:
            mocked.get(_CARRIER_LIST_URL, exception=aiohttp.ClientConnectionError("boom"))
            with pytest.raises(Track17ConnectionError):
                await catalog.load(session)


async def test_non_array_payload_raises_api_error() -> None:
    catalog = CarrierCatalog()
    async with aiohttp.ClientSession() as session:
        with aioresponses() as mocked:
            mocked.get(_CARRIER_LIST_URL, payload={"unexpected": "object"})
            with pytest.raises(Track17APIError, match="array"):
                await catalog.load(session)


async def test_invalid_json_raises_api_error() -> None:
    catalog = CarrierCatalog()
    async with aiohttp.ClientSession() as session:
        with aioresponses() as mocked:
            mocked.get(_CARRIER_LIST_URL, body="<html>cdn error</html>")
            with pytest.raises(Track17APIError, match="JSON"):
                await catalog.load(session)
