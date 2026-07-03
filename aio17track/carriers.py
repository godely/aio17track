"""``CarrierCatalog`` — carrier code <-> name lookup (SPEC §10).

Lazily fetches ``carrier.all.json`` from 17track's CDN and caches it in
memory, with an optional on-disk cache path so consumers can avoid
refetching. Not required for core client calls (carrier is an int
everywhere); this is a convenience for display.
"""

from collections.abc import Mapping
from pathlib import Path

import aiohttp


class CarrierCatalog:
    """Lazy, cached carrier code/name catalog."""

    def __init__(self, *, cache_path: Path | None = None) -> None:
        raise NotImplementedError

    async def load(self, session: aiohttp.ClientSession) -> None:
        raise NotImplementedError

    def name(self, code: int) -> str | None:
        raise NotImplementedError

    def code(self, name: str) -> int | None:
        raise NotImplementedError

    def all(self) -> Mapping[int, str]:
        raise NotImplementedError
