# aio17track — Library Specification

A dependency-light, async Python wrapper for the 17TRACK Tracking API **v2.4**.
This document is the source of truth. Signatures and rules here are decisions, not suggestions. Do not invent alternative interfaces.

---

## 1. Purpose & scope

Wrap the official 17TRACK Tracking API v2.4 (`https://api.17track.net/track/v2.4/`) as a typed, async Python client, plus a standalone webhook-verification helper.

**In scope:** all v2.4 endpoints, batch + rate-limit handling, a typed data model, a typed error model, webhook signature verification and parsing, carrier code lookup.

**Non-goals:**
- No Home Assistant code. This package must not import `homeassistant`. It is consumed by a separate HA component later, and by anyone else.
- No consumer-account (email/password) path. That is the incumbent's reverse-engineered approach and is explicitly excluded.
- No persistence, no scheduling, no CLI in v0.1 **on `master`**. The client makes calls; the caller owns state. (An experimental CLI exists on the `dev` branch as a documented deviation — see §14.)

---

## 2. Hard constraints (from the API contract, verified)

- **Auth:** header `17token: <api_key>` on every request. No cookies, no login step.
- **Method:** every endpoint is `POST` with a JSON body.
- **Rate limit:** 3 requests/second. Excess returns HTTP 429. The client must self-throttle so callers never have to.
- **Batch size:** mutation/read-by-number endpoints accept **at most 40 numbers per call**. `gettracklist` accepts up to 200 numbers as a filter and paginates at 40/page.
- **Envelope:** every response is `{ "code": <int>, "data": { "accepted": [...], "rejected": [...] } }` (list endpoints add a `page` object). Partial success is normal: some numbers land in `accepted`, others in `rejected` with a per-item error code. This is not an exception; it is data.
- **Quota:** 1 credit per successful registration. Continued tracking, reads, and webhook pushes cost nothing. `getRealTimeTrackInfo` costs 1 credit (Standard) or 10 credits (Instant) per call. `getquota` is the only source of truth for balance; never estimate it locally.

---

## 3. Package layout

```
aio17track/
  __init__.py       # public exports only
  client.py         # Track17Client — the façade
  transport.py      # _Transport — auth, timeout, throttle, retry, envelope unwrap
  models.py         # frozen slotted dataclasses + from_api() constructors
  enums.py          # MainStatus, SubStatus, TrackingStatus, PushStatus, ErrorCode
  errors.py         # exception taxonomy
  webhook.py        # verify_signature(), parse_event()
  carriers.py       # CarrierCatalog — lazy fetch + cache of carrier.all.json
py.typed            # ship type info (PEP 561)
```

**Dependency policy:** runtime dependency is `aiohttp` only. No pydantic, no attrs, no requests. Models are hand-written frozen `@dataclass(slots=True, frozen=True)`. Rationale: the eventual HA-core path pins library deps globally; a zero-transitive-dependency library never conflicts with other integrations.

**Python floor:** 3.12 (enables `type` aliases, `override`, modern generics without ceremony).

---

## 4. Transport (`transport.py`)

Internal, not part of the public API. Owns all HTTP concerns.

- Accepts an optional external `aiohttp.ClientSession`. Creates its own only as a fallback and closes only what it created.
- Injects the `17token` header and a JSON content type on every call.
- **Throttle:** token-bucket limiter at 3 req/s, applied before every request, so batched chunks are paced automatically.
- **Retry:** on 429 and 5xx, retry with exponential backoff + jitter, honoring `Retry-After` when present. Cap attempts (default 3). Non-retryable 4xx fail fast.
- **Envelope unwrap:** parse the outer `code`. If `code != 0` at the request level, raise the mapped exception (see §7). If `code == 0`, hand `data` back to the caller for accepted/rejected splitting. The transport never inspects per-item rejections; that belongs to the model layer.
- Timeout: default total 30s (the API's documented max), configurable.

---

## 5. Client (`client.py`)

`Track17Client` is the only façade callers touch. Design rules: no I/O in `__init__`, every public method is `async`, callers pass any number of items and the client chunks to 40 internally.

```python
class Track17Client:
    def __init__(
        self,
        api_key: str,
        *,
        session: aiohttp.ClientSession | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None: ...

    # --- registration lifecycle ---
    async def register(
        self, items: Sequence[TrackRegistration]
    ) -> BatchResult[RegisteredNumber]: ...

    async def stop_track(
        self, items: Sequence[NumberCarrier]
    ) -> BatchResult[NumberCarrier]: ...

    async def retrack(
        self, items: Sequence[NumberCarrier]
    ) -> BatchResult[NumberCarrier]: ...

    async def delete_track(
        self, items: Sequence[NumberCarrier]
    ) -> BatchResult[NumberCarrier]: ...

    async def change_carrier(
        self, items: Sequence[CarrierChange]
    ) -> BatchResult[NumberCarrier]: ...

    async def change_info(
        self, items: Sequence[InfoChange]
    ) -> BatchResult[NumberCarrier]: ...

    # --- reads ---
    async def get_track_info(
        self, items: Sequence[NumberCarrier]
    ) -> BatchResult[TrackInfo]: ...

    async def get_track_list(
        self,
        *,
        number_filter: Sequence[str] | None = None,
        tracking_status: TrackingStatus | None = None,
        package_status: MainStatus | None = None,
        page_no: int = 1,
    ) -> TrackListPage: ...

    async def get_quota(self) -> Quota: ...

    # --- metered, guarded ---
    async def get_realtime_track_info(
        self,
        items: Sequence[NumberCarrier],
        *,
        cache_level: CacheLevel = CacheLevel.STANDARD,  # never defaults to INSTANT
    ) -> BatchResult[TrackInfo]: ...

    async def close(self) -> None: ...   # closes only a client-owned session

    async def __aenter__(self) -> "Track17Client": ...
    async def __aexit__(self, *exc: object) -> None: ...
```

Batching contract: `register`, `stop_track`, `retrack`, `delete_track`, `change_carrier`, `change_info`, `get_track_info`, `get_realtime_track_info` all accept unbounded input, split into chunks of 40, dispatch through the throttle, and merge results into a single `BatchResult`. Accepted and rejected items from every chunk are concatenated.

`CacheLevel.INSTANT` must never be the default anywhere and should carry a docstring warning that it deducts 10 credits per call.

---

## 6. Data models (`models.py`)

All frozen, slotted. Each has a `from_api(raw: dict) -> Self` classmethod that does the field mapping and type coercion. Callers never construct these from raw dicts themselves.

### Inputs
```python
@dataclass(slots=True, frozen=True)
class TrackRegistration:
    number: str
    carrier: int | None = None          # omit -> 17track auto-detects
    tag: str | None = None
    order_no: str | None = None
    lang: str | None = None
    param: str | None = None            # extra carrier param (phone/zip), when required

@dataclass(slots=True, frozen=True)
class NumberCarrier:
    number: str
    carrier: int | None = None

@dataclass(slots=True, frozen=True)
class CarrierChange:
    number: str
    carrier_old: int
    carrier_new: int

@dataclass(slots=True, frozen=True)
class InfoChange:
    number: str
    carrier: int | None = None
    tag: str | None = None
    order_no: str | None = None
```

### Outputs
```python
@dataclass(slots=True, frozen=True)
class RegisteredNumber:
    number: str
    carrier: int
    tracking_status: TrackingStatus
    package_status: MainStatus
    register_time: datetime | None
    tag: str | None
    order_no: str | None

@dataclass(slots=True, frozen=True)
class TrackEvent:
    time_iso: datetime | None       # parsed, timezone-aware
    time_raw: str | None            # preserved verbatim; delivery time lives here for Delivered_Other
    description: str | None
    location: str | None
    stage: str | None
    sub_status: SubStatus
    coordinates: tuple[float, float] | None

@dataclass(slots=True, frozen=True)
class LatestStatus:
    status: MainStatus
    sub_status: SubStatus

@dataclass(slots=True, frozen=True)
class TrackInfo:
    number: str
    carrier: int
    latest_status: LatestStatus
    latest_event: TrackEvent | None
    events: tuple[TrackEvent, ...]          # full history, newest-first
    shipping_country: str | None
    recipient_country: str | None
    tracking_number_extra: dict[str, Any]   # anything version-new we don't model yet
    stopped: bool

@dataclass(slots=True, frozen=True)
class Quota:
    remaining: int
    used: int | None
    total: int | None

@dataclass(slots=True, frozen=True)
class RejectedItem:
    number: str
    carrier: int | None
    error_code: ErrorCode
    error_message: str

@dataclass(slots=True, frozen=True)
class BatchResult(Generic[T]):
    accepted: tuple[T, ...]
    rejected: tuple[RejectedItem, ...]

    @property
    def already_registered(self) -> tuple[RejectedItem, ...]:
        # derived view: rejections whose code == ErrorCode.ALREADY_REGISTERED (-18019901)
        # for an HA integration, "already registered" is success, not failure
        ...

    @property
    def ok(self) -> bool:
        return not self.rejected

@dataclass(slots=True, frozen=True)
class TrackListPage:
    items: tuple[RegisteredNumber, ...]
    page_no: int
    page_total: int
    data_total: int
```

**Timestamp rule:** parse `time_iso` / `register_time` / `track_time` into aware `datetime`. Preserve `time_raw` untouched. Per the docs, when `sub_status == "Delivered_Other"`, `time_raw` holds the true delivery time. Never fabricate a timezone the API did not send.

---

## 7. Error model (`errors.py` + `ErrorCode` in `enums.py`)

Two distinct planes.

**Plane 1 — request-level failures raise.** These abort the whole call.
```python
Track17Error(Exception)                 # base
├── Track17ConnectionError              # network/timeout, from aiohttp ClientError
├── AuthenticationError                 # 401, -18010002, -18010001 (IP whitelist), -18010004 (disabled)
├── RateLimitError                      # 429; carries retry_after: float | None
├── QuotaExhaustedError                 # -18019907, -18019908
├── SignatureError                      # webhook verification failed (raised by webhook.py)
└── Track17APIError                     # any other non-zero request-level code; carries code + message
```

**Plane 2 — item-level rejections do not raise.** They arrive typed inside `BatchResult.rejected` as `RejectedItem`, keyed by `ErrorCode`. Example: `-18019901` (already registered), `-18019902` (retrack not allowed), `-18010013` (invalid carrier). Callers decide what a given rejection means for them.

`ErrorCode(IntEnum)` enumerates the full documented code table (~45 codes) so callers match on names, not magic negatives. Unknown codes map to `ErrorCode.UNKNOWN` carrying the raw int.

---

## 8. Enums (`enums.py`)

`MainStatus`, `SubStatus`, `TrackingStatus`, `PushStatus`, `CacheLevel`, `ErrorCode`.

**Forward-compatibility rule, non-negotiable:** every enum parse falls back to an `UNKNOWN` member that carries the raw string/int. Never raise on an unrecognized status. The incumbent library crashes on new statuses (`'NoneType' object has no attribute 'get'`); reproducing that is a defect.

Pull the authoritative status list from the v2.4 docs at build time (currently 9 main statuses and roughly 30 sub-statuses; the count has changed between versions, so do not hardcode from memory). `TrackingStatus` ∈ {Tracking, Stopped, ...}. `PushStatus` reflects webhook delivery outcome. `CacheLevel` ∈ {STANDARD, INSTANT}.

---

## 9. Webhook helper (`webhook.py`)

Standalone, no client instance required. Two functions.

```python
def verify_signature(raw_body: bytes, sign_header: str, api_key: str) -> bool:
    # signed string = raw_body_text + "/" + api_key
    # expected = sha256(signed_string).hexdigest()
    # compare against sign_header with hmac.compare_digest (constant-time)
    ...

def parse_event(raw_body: bytes) -> WebhookEvent:
    # returns WebhookEvent(event: WebhookEventType, data: TrackInfo | StoppedNotice)
    # WebhookEventType ∈ {TRACKING_UPDATED, TRACKING_STOPPED}
    ...
```

**Critical:** verification runs over the **raw request bytes**, before any JSON parse/re-serialize. If you parse then re-dump, key ordering or whitespace shifts and the hash breaks. The signing string is `<raw message>/<api_key>`, SHA256, hex. Use `hmac.compare_digest` for the comparison to avoid timing leaks. Signature failure raises `SignatureError`; malformed body raises `Track17APIError`.

**Open question to confirm at build time:** whether the callback URL can be set via API or only in the dashboard. Current belief: dashboard only. This does not affect the library (it only verifies + parses), but the HA component depends on the answer.

---

## 10. Carrier catalog (`carriers.py`)

```python
class CarrierCatalog:
    async def load(self, session: aiohttp.ClientSession) -> None: ...
    def name(self, code: int) -> str | None: ...
    def code(self, name: str) -> int | None: ...
    def all(self) -> Mapping[int, str]: ...
```

Lazily fetches `carrier.all.json` from 17track's CDN, caches in memory. Optional on-disk cache path so consumers can avoid refetching. Not required for core client calls (carrier is an int everywhere); this is a convenience for display.

---

## 11. Testing

Three layers.

1. **Unit** — `aioresponses` mocking every endpoint against captured fixtures. Seed fixtures from the real session on 2026-07-03: Correios (carrier 2151), YanWen (190012), and the `Exception` / `Exception_Returning` payload. Include a fixture with a mixed `accepted` + `rejected` response, and one with `-18019901` (already registered).
2. **Property** — enum fallback (random unknown strings never raise, always land in UNKNOWN), and chunking (N items always split into ceil(N/40) chunks, all items preserved, order stable).
3. **Live, opt-in** — gated on env var `SEVENTEENTRACK_LIVE_KEY`. Exercises register → get_track_info → get_quota → delete_track against a throwaway number, deleting it at the end to return the slot. Skipped by default.
4. **Webhook** — a recorded raw body + its real `sign` header, asserting byte-exact verification passes, and that a single mutated byte fails.

Coverage target: 95%+ on models, enums, errors, webhook. Transport retry/throttle tested with fake clocks.

---

## 12. Packaging & tooling

- Build: `uv` + `hatchling`. `pyproject.toml` only, no setup.py.
- Lint/format: `ruff`. Types: `mypy --strict`, clean.
- License: Apache-2.0.
- CI: GitHub Actions matrix over 3.12 / 3.13. Jobs: ruff, mypy, pytest (unit + property; live suite excluded).
- Release: tagged, PyPI via trusted publishing (OIDC, no stored token).
- Versioning: semver from 0.1.0. The HA manifest will pin exact versions, so breaking changes must bump major.
- Ship `py.typed`.

---

## 13. Build order (milestones)

Do these in sequence. Each is independently reviewable.

- **M0 — Scaffold.** Repo, `pyproject.toml`, tooling, CI, empty modules with stubbed signatures matching §5–§10. No bodies. `mypy --strict` passes on the stubs.
- **M1 — Models + enums + errors.** Pure data layer, fully unit-tested against fixtures. No network.
- **M2 — Transport.** Auth, throttle, retry, envelope unwrap. Tested with mocked responses and fake clocks.
- **M3 — Client reads.** `get_quota`, `get_track_info`, `get_track_list`. Wire batching + BatchResult splitting.
- **M4 — Client mutations.** `register`, `stop_track`, `retrack`, `delete_track`, `change_carrier`, `change_info`.
- **M5 — Webhook + carriers.** Signature verification, event parsing, carrier catalog.
- **M6 — Realtime (guarded) + polish.** `get_realtime_track_info`, docstrings, README, live suite, 0.1.0 tag.

Stop after M0 for review before implementing bodies.

---

## 14. Branching & deviations

Two long-lived branches:

- **`master` is the SPEC-conformant mainline.** Every signature and rule in
  this document is binding there. Releases (including the 0.1.0 tag) are cut
  from `master` only. Judgment calls that *fill gaps* in this SPEC (mapping
  decisions the SPEC doesn't pin down) belong on `master`, flagged in the PR.
- **`dev` is the deviation line.** Features that *contradict* this SPEC live
  there: branch off `dev`, PR back into `dev`, never target `master`
  directly. `master` is merged into `dev` periodically so the experimental
  line tracks the mainline. `dev` runs the same CI as `master`.
- **Promotion requires amendment.** Moving a deviation from `dev` to
  `master` requires updating this SPEC (in or before the promotion PR) so
  `master` and SPEC never disagree.

Current deviations on `dev`:

- **CLI (M7).** An `argparse`-based command-line interface (`aio17track`
  console script + `python -m aio17track`) covering every client method,
  carrier lookups, and webhook verify/parse. Stdlib-only, so the §3
  dependency policy holds; the library's public API surface is unchanged.
  Supersedes §1's "no CLI" only on `dev`.
