"""``CarrierCatalog`` — carrier code <-> name lookup (SPEC §10).

Lazily fetches the carrier list from 17track's CDN and caches it in
memory, with an optional on-disk cache path so consumers can avoid
refetching. Not required for core client calls (carrier is an int
everywhere); this is a convenience for display.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import aiohttp

from .errors import Track17APIError, Track17ConnectionError

# SPEC §10 names carrier.all.json, but this catalog exists to map names to
# the integer codes the API's `carrier` parameter accepts — and the API's
# own -18019903 error message directs users to apicarrier.all.json for
# exactly those keys, so that is the list fetched here (deliberate SPEC
# deviation). TODO(M6): sanity-check a few keys against a live account.
_CARRIER_LIST_URL = "https://res.17track.net/asset/carrier/info/apicarrier.all.json"


class CarrierCatalog:
    """Lazy, cached carrier code/name catalog.

    ``await load(session)`` once (idempotent), then ``name``/``code``/``all``
    are synchronous lookups. Lookups before ``load`` raise ``RuntimeError``.
    """

    def __init__(self, *, cache_path: Path | None = None) -> None:
        self._cache_path = cache_path
        self._by_code: dict[int, str] | None = None
        self._by_name: dict[str, int] = {}

    async def load(self, session: aiohttp.ClientSession) -> None:
        """Fetch the carrier list (or read the on-disk cache) and index it."""
        if self._by_code is not None:
            return
        cached = self._read_cache()
        if cached is not None:
            try:
                self._index(cached)
            except Track17APIError:
                pass  # poisoned cache (valid JSON, wrong shape): refetch
            else:
                return
        raw = await self._fetch(session)
        # Index before persisting: only a payload that validated as a
        # carrier list is written, so a bad CDN response (valid JSON but
        # not the array) can never poison the cache.
        self._index(raw)
        if self._cache_path is not None:
            self._cache_path.write_text(json.dumps(raw), encoding="utf-8")

    def _read_cache(self) -> Any | None:
        if self._cache_path is None or not self._cache_path.exists():
            return None
        try:
            return json.loads(self._cache_path.read_text(encoding="utf-8"))
        except ValueError:
            return None  # unreadable cache: fall through to a fresh fetch

    async def _fetch(self, session: aiohttp.ClientSession) -> Any:
        try:
            async with session.get(_CARRIER_LIST_URL) as response:
                if response.status != 200:
                    raise Track17APIError(
                        response.status,
                        f"HTTP {response.status} fetching the carrier list",
                    )
                try:
                    return await response.json(content_type=None)
                except ValueError as exc:
                    raise Track17APIError(-1, "carrier list is not valid JSON") from exc
        except TimeoutError as exc:
            raise Track17ConnectionError("carrier list request timed out") from exc
        except aiohttp.ClientError as exc:
            raise Track17ConnectionError(str(exc)) from exc

    def _index(self, raw: Any) -> None:
        if not isinstance(raw, list):
            raise Track17APIError(-1, "carrier list is not a JSON array")
        by_code: dict[int, str] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            key = entry.get("key")
            name = entry.get("_name") or entry.get("name")
            if isinstance(key, int) and isinstance(name, str) and name:
                by_code[key] = name
        self._by_code = by_code
        self._by_name = {name.casefold(): code for code, name in by_code.items()}

    def _require_loaded(self) -> dict[int, str]:
        if self._by_code is None:
            raise RuntimeError("CarrierCatalog.load() has not been awaited yet")
        return self._by_code

    def name(self, code: int) -> str | None:
        """Carrier display name for an API code, or None if unknown."""
        return self._require_loaded().get(code)

    def code(self, name: str) -> int | None:
        """API code for a carrier name (case-insensitive), or None if unknown."""
        self._require_loaded()
        return self._by_name.get(name.casefold())

    def all(self) -> Mapping[int, str]:
        """Read-only view of the full code -> name mapping."""
        return MappingProxyType(self._require_loaded())
