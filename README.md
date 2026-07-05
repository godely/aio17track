# aio17track

Dependency-light, fully typed async Python client for the
[17TRACK Tracking API v2.4](https://api.17track.net/). One façade
(`Track17Client`) wraps every v2.4 endpoint with built-in 3 req/s
throttling, automatic 40-number batching, frozen-dataclass models, a typed
error taxonomy, standalone webhook signature verification, and a carrier
code catalog. The only runtime dependency is `aiohttp`.

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

Batch calls accept any number of items — the client chunks to the API's
40-number limit internally and paces requests at 3/s, so callers never
handle rate limits themselves. Partial success is data, not an exception:
every batch returns a `BatchResult` with `accepted` and `rejected` sides
(`result.already_registered` filters the rejection that usually means
"fine, carry on").

### Webhooks

Verification runs over the **raw request bytes** — pass them straight
through, never re-serialize:

```python
from aio17track import parse_event, verify_signature

verify_signature(raw_body, request.headers["sign"], "your-17token-api-key")
event = parse_event(raw_body)  # TrackInfo or StoppedNotice in event.data
```

### Carrier names

```python
from aio17track.carriers import CarrierCatalog

catalog = CarrierCatalog()          # optional: cache_path=Path("carriers.json")
await catalog.load(session)
catalog.name(2151)                  # "Correios"
catalog.code("correios")            # 2151
```

### Realtime lookups (metered)

> **Warning — credits:** `get_realtime_track_info` deducts credits per
> number: 1 with the default `CacheLevel.STANDARD`, **10 with
> `CacheLevel.INSTANT`**. INSTANT is never the default and must be opted
> into explicitly.

```python
fresh = await client.get_realtime_track_info(
    [NumberCarrier("RR123456789BR", carrier=2151)],
    # cache_level=CacheLevel.INSTANT  # only if you accept the 10-credit cost
)
```

## CLI

Everything above is also drivable with no code. The CLI is
[Typer](https://typer.tiangolo.com/)-based and ships as an optional extra,
so the base library keeps `aiohttp` as its only runtime dependency:

```sh
uv tool install "aio17track[cli]"   # or: pip install "aio17track[cli]"
```

Run it as `aio17track` (or `python -m aio17track`). Every command has rich
`--help`; shell completion comes via `aio17track --install-completion`.
Log in once and the key is stored (permissions `0600`) in the per-user app
directory — no flag or environment variable needed afterwards. `--key` and
`$SEVENTEENTRACK_KEY` still work and take precedence, for scripts and CI:

```sh
aio17track auth login     # prompts for the key, verifies it, stores it
aio17track auth status    # which key would be used, and from where
aio17track auth logout    # delete the stored key

aio17track quota
aio17track register RR123456789BR --carrier 2151 --tag my-order
aio17track info RR123456789BR --carrier 2151 --events
aio17track list --tracking-status Tracking
aio17track delete RR123456789BR
aio17track carriers --search correios
aio17track realtime RR123456789BR --carrier 2151   # 1 credit
# --instant deducts 10 credits per number — deliberate flag, never default
```

Add `--json` after any subcommand for machine-readable output. Exit codes:
`0` success (including partial success — check the `rejected:` lines),
`1` API failure, `2` usage error.

`carriers` keeps a local copy of the carrier list in the per-user app
directory (`~/.config/aio17track/carriers.json` on Linux) and refetches it
automatically once the copy is older than 7 days. Pass `--refresh` to force
a fresh download sooner. The cache is best-effort: if the app directory
isn't writable, lookups still work (uncached, with a warning). The old
`--cache PATH` override still works but is deprecated — the cache is on by
default now.

## Errors

Request-level failures raise (`AuthenticationError`, `RateLimitError`,
`QuotaExhaustedError`, `Track17ConnectionError`, `Track17APIError`,
`SignatureError` — all under `Track17Error`). Per-item rejections never
raise; they arrive typed in `BatchResult.rejected` keyed by `ErrorCode`.
Unknown statuses and error codes never crash parsing: enums fall back to
an `UNKNOWN` member carrying the raw value.

## Development

```sh
uv sync
uv run ruff check .
uv run mypy --strict aio17track tests
uv run pytest                                        # unit + property suites
SEVENTEENTRACK_LIVE_KEY=<key> uv run pytest -m live  # opt-in, costs 1 credit
```

## License

Apache-2.0.
