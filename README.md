# aio17track

Dependency-light, fully typed async Python client for the
[17TRACK Tracking API v2.4](https://api.17track.net/). One façade
(`Track17Client`) wraps every v2.4 endpoint with built-in 3 req/s
throttling, automatic 40-number batching, frozen-dataclass models, a typed
error taxonomy, and standalone webhook signature verification. The only
runtime dependency is `aiohttp`.

> **Status:** milestone M0 — typed scaffolding only. Signatures are final
> per [SPEC.md](SPEC.md); function bodies are not implemented yet.

## Install

```sh
uv add aio17track
# or
pip install aio17track
```

Requires Python 3.12+.

## Usage

```python
import asyncio

from aio17track import NumberCarrier, Track17Client, TrackRegistration


async def main() -> None:
    async with Track17Client("your-17token-api-key") as client:
        result = await client.register(
            [TrackRegistration(number="RR123456789BR", carrier=2151)]
        )
        for rejected in result.rejected:
            print(rejected.number, rejected.error_code, rejected.error_message)

        info = await client.get_track_info(
            [NumberCarrier(number="RR123456789BR", carrier=2151)]
        )
        for track in info.accepted:
            print(track.latest_status.status, track.latest_event)

        quota = await client.get_quota()
        print(f"{quota.remaining} credits remaining")


asyncio.run(main())
```

> **Warning — metered call:** `get_realtime_track_info` with
> `cache_level=CacheLevel.INSTANT` deducts **10 credits per call**
> (`CacheLevel.STANDARD` deducts 1). `INSTANT` is never the default and
> must be opted into explicitly.

## License

Apache-2.0.
