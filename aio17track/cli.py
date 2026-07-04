"""Command-line interface — drive the client with no code.

Typer-based, shipped as the optional ``cli`` extra:
``pip install "aio17track[cli]"``. A thin layer over the public API: parse
arguments, make the call, print. Human-readable lines by default; ``--json``
emits machine-readable output. The API key comes from ``--key``, the
``SEVENTEENTRACK_KEY`` environment variable, or — after a one-time
``aio17track auth login`` — the key stored in the per-user app directory,
in that order of precedence.

Exit codes: 0 success, 1 API/signature/lookup failure, 2 usage error.
"""

import asyncio
import dataclasses
import json
import os
import sys
import time
from collections.abc import Callable, Coroutine
from datetime import datetime
from enum import Enum, StrEnum
from pathlib import Path
from typing import Annotated, Any

try:
    import typer
except ModuleNotFoundError as exc:  # pragma: no cover - exercised only sans extra
    raise SystemExit(
        "the aio17track CLI requires the 'cli' extra: "
        'install with `pip install "aio17track[cli]"` (or `uv add "aio17track[cli]"`)'
    ) from exc

import aiohttp

from .carriers import CarrierCatalog
from .client import Track17Client
from .enums import CacheLevel, MainStatus, TrackingStatus
from .errors import SignatureError, Track17Error
from .models import (
    BatchResult,
    CarrierChange,
    InfoChange,
    NumberCarrier,
    RegisteredNumber,
    TrackEvent,
    TrackInfo,
    TrackRegistration,
)
from .webhook import parse_event, verify_signature

_KEY_ENV_VAR = "SEVENTEENTRACK_KEY"

# The carrier list changes rarely; a week-old copy is fresh enough for
# name/code lookups, and --refresh exists for anyone who can't wait.
_CARRIER_CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

app = typer.Typer(
    name="aio17track",
    help="Command-line client for the 17TRACK Tracking API v2.4.",
    no_args_is_help=True,
)

auth_app = typer.Typer(
    help="Manage the stored API key (login once, then no --key needed).",
    no_args_is_help=True,
)
app.add_typer(auth_app, name="auth")

# --- shared parameter declarations ---

_KeyOption = Annotated[
    str | None,
    typer.Option("--key", envvar=_KEY_ENV_VAR, show_envvar=True, help="17token API key"),
]
_JsonOption = Annotated[bool, typer.Option("--json", help="emit JSON output")]
_CarrierOption = Annotated[int | None, typer.Option("--carrier", help="carrier code")]
_EventsOption = Annotated[
    bool, typer.Option("--events", help="print the full event history")
]
_NumbersArgument = Annotated[list[str], typer.Argument(help="tracking numbers")]


# CLI-facing choice enums: the library enums' documented members minus the
# UNKNOWN parse-fallback (a test pins these to the library tables).
class _TrackingStatusChoice(StrEnum):
    TRACKING = "Tracking"
    STOPPED = "Stopped"


class _PackageStatusChoice(StrEnum):
    NOT_FOUND = "NotFound"
    INFO_RECEIVED = "InfoReceived"
    IN_TRANSIT = "InTransit"
    EXPIRED = "Expired"
    AVAILABLE_FOR_PICKUP = "AvailableForPickup"
    OUT_FOR_DELIVERY = "OutForDelivery"
    DELIVERY_FAILURE = "DeliveryFailure"
    DELIVERED = "Delivered"
    EXCEPTION = "Exception"


def _stored_key_path() -> Path:
    return Path(typer.get_app_dir("aio17track")) / "api-key"


def _read_stored_key() -> str | None:
    """Best-effort read of the key saved by ``auth login``."""
    try:
        stored = _stored_key_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return stored or None


def _mask_key(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def _require_key(key: str | None) -> str:
    if key:  # --key flag or the environment variable (typer merges both)
        return key
    stored = _read_stored_key()
    if stored:
        return stored
    typer.echo(
        "error: no API key: run `aio17track auth login`, pass --key, "
        f"or set {_KEY_ENV_VAR} in the environment",
        err=True,
    )
    raise typer.Exit(2)


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a client coroutine, mapping failures to CLI exit codes."""
    try:
        return asyncio.run(coro)
    except ValueError as exc:  # client-side guards (filter caps, cache level, ...)
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except OSError as exc:  # unreadable/unwritable local paths (e.g. carriers --cache)
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except Track17Error as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc


# --- output helpers ---


def _jsonable(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return value


def _emit_json(value: object) -> None:
    print(json.dumps(_jsonable(value), indent=2, ensure_ascii=False))


def _format_event(event: TrackEvent) -> str:
    time = event.time_iso.isoformat() if event.time_iso else (event.time_raw or "unknown time")
    location = f" @ {event.location}" if event.location else ""
    return f"[{time}] {event.sub_status}: {event.description or '(no description)'}{location}"


def _format_track_info(info: TrackInfo, *, events: bool = False) -> str:
    lines = [
        f"{info.number} (carrier {info.carrier})  "
        f"{info.latest_status.status} / {info.latest_status.sub_status}"
    ]
    if info.latest_event is not None:
        lines.append(f"    latest: {_format_event(info.latest_event)}")
    if events:
        lines.extend(f"    {_format_event(event)}" for event in info.events)
    return "\n".join(lines)


def _format_registered(item: RegisteredNumber) -> str:
    parts = [f"{item.number} (carrier {item.carrier})"]
    if not item.tracking_status.is_unknown:
        parts.append(f"{item.tracking_status} / {item.package_status}")
    if item.register_time is not None:
        parts.append(f"registered {item.register_time.isoformat()}")
    if item.tag:
        parts.append(f"tag={item.tag}")
    return "  ".join(parts)


def _format_number_carrier(item: NumberCarrier) -> str:
    return f"{item.number} (carrier {item.carrier})"


def _print_batch[T](
    as_json: bool, result: BatchResult[T], describe: Callable[[T], str]
) -> None:
    if as_json:
        _emit_json(result)
        return
    for item in result.accepted:
        print(f"accepted: {describe(item)}")
    for rejected in result.rejected:
        print(
            f"rejected: {rejected.number}  [{int(rejected.error_code)}] "
            f"{rejected.error_code.name}: {rejected.error_message}"
        )


def _default_carrier_cache_path() -> Path:
    return Path(typer.get_app_dir("aio17track")) / "carriers.json"


def _carrier_cache_is_stale(path: Path) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age > _CARRIER_CACHE_MAX_AGE_SECONDS


def _read_body(path: str) -> bytes:
    if path == "-":
        return sys.stdin.buffer.read()
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc


# --- auth ---


@auth_app.command("login")
def auth_login(
    key: Annotated[
        str | None,
        typer.Option("--key", help="API key to store (prompted securely if omitted)"),
    ] = None,
) -> None:
    """Verify an API key against the API and store it for future runs."""
    candidate = (key or typer.prompt("17TRACK API key", hide_input=True)).strip()
    if not candidate:
        typer.echo("error: empty key", err=True)
        raise typer.Exit(2)

    async def call() -> Any:
        async with Track17Client(candidate) as client:
            return await client.get_quota()

    quota_result = _run(call())  # a rejected key exits 1 here, before anything is stored

    path = _stored_key_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Created 0600 from the first byte; a write-then-chmod would leave a
        # umask-wide window. The chmod covers a pre-existing file, where
        # O_CREAT's mode does not apply.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(candidate + "\n")
        path.chmod(0o600)
    except OSError as exc:
        typer.echo(f"error: could not store the key: {exc}", err=True)
        raise typer.Exit(2) from exc
    print(f"key verified (remaining={quota_result.remaining} credits), saved to {path}")


@auth_app.command("status")
def auth_status(as_json: _JsonOption = False) -> None:
    """Show which API key the CLI would use and where it comes from."""
    env_key = os.environ.get(_KEY_ENV_VAR) or None
    stored_key = _read_stored_key()
    source = "environment" if env_key else ("stored" if stored_key else None)
    if as_json:
        _emit_json(
            {
                "source": source,
                "environment": env_key is not None,
                "stored": stored_key is not None,
                "path": str(_stored_key_path()),
            }
        )
        if source is None:
            raise typer.Exit(1)
        return
    if source is None:
        typer.echo(
            f"no API key configured: run `aio17track auth login` or set {_KEY_ENV_VAR}",
            err=True,
        )
        raise typer.Exit(1)
    active = env_key or stored_key
    assert active is not None
    origin = f"${_KEY_ENV_VAR}" if env_key else _stored_key_path()
    print(f"key {_mask_key(active)} from {origin}")
    if env_key and stored_key:
        print(f"(a stored key also exists at {_stored_key_path()}; the environment wins)")


@auth_app.command("logout")
def auth_logout() -> None:
    """Delete the stored API key."""
    path = _stored_key_path()
    try:
        path.unlink()
    except FileNotFoundError:
        print("no stored key")
        return
    except OSError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    print(f"removed {path}")


# --- commands ---


@app.command()
def quota(key: _KeyOption = None, as_json: _JsonOption = False) -> None:
    """Show the credit balance."""
    api_key = _require_key(key)

    async def call() -> Any:
        async with Track17Client(api_key) as client:
            return await client.get_quota()

    result = _run(call())
    if as_json:
        _emit_json(result)
        return
    used = "?" if result.used is None else result.used
    total = "?" if result.total is None else result.total
    print(f"remaining={result.remaining} used={used} total={total}")


@app.command()
def register(
    numbers: _NumbersArgument,
    key: _KeyOption = None,
    as_json: _JsonOption = False,
    carrier: _CarrierOption = None,
    tag: Annotated[str | None, typer.Option("--tag")] = None,
    order_no: Annotated[str | None, typer.Option("--order-no")] = None,
    lang: Annotated[str | None, typer.Option("--lang")] = None,
    param: Annotated[
        str | None,
        typer.Option(
            "--param", help="extra carrier parameter (phone/zip), when the carrier requires it"
        ),
    ] = None,
) -> None:
    """Register numbers for tracking (1 credit each)."""
    api_key = _require_key(key)
    registrations = [
        TrackRegistration(
            number=number, carrier=carrier, tag=tag, order_no=order_no, lang=lang, param=param
        )
        for number in numbers
    ]

    async def call() -> BatchResult[RegisteredNumber]:
        async with Track17Client(api_key) as client:
            return await client.register(registrations)

    _print_batch(as_json, _run(call()), _format_registered)


@app.command()
def info(
    numbers: _NumbersArgument,
    key: _KeyOption = None,
    as_json: _JsonOption = False,
    carrier: _CarrierOption = None,
    events: _EventsOption = False,
) -> None:
    """Get tracking info for registered numbers."""
    api_key = _require_key(key)
    items = [NumberCarrier(number, carrier=carrier) for number in numbers]

    async def call() -> BatchResult[TrackInfo]:
        async with Track17Client(api_key) as client:
            return await client.get_track_info(items)

    _print_batch(as_json, _run(call()), lambda item: _format_track_info(item, events=events))


@app.command()
def realtime(
    numbers: _NumbersArgument,
    key: _KeyOption = None,
    as_json: _JsonOption = False,
    carrier: _CarrierOption = None,
    events: _EventsOption = False,
    instant: Annotated[
        bool,
        typer.Option(
            "--instant",
            help="INSTANT cache level: fresh carrier fetch, DEDUCTS 10 CREDITS PER NUMBER",
        ),
    ] = False,
) -> None:
    """Metered realtime lookup (1 credit per number; 10 with --instant)."""
    api_key = _require_key(key)
    items = [NumberCarrier(number, carrier=carrier) for number in numbers]
    cache_level = CacheLevel.INSTANT if instant else CacheLevel.STANDARD

    async def call() -> BatchResult[TrackInfo]:
        async with Track17Client(api_key) as client:
            return await client.get_realtime_track_info(items, cache_level=cache_level)

    _print_batch(as_json, _run(call()), lambda item: _format_track_info(item, events=events))


@app.command("list")
def list_(
    key: _KeyOption = None,
    as_json: _JsonOption = False,
    number: Annotated[
        list[str] | None, typer.Option("--number", help="filter by number (repeatable)")
    ] = None,
    tracking_status: Annotated[
        _TrackingStatusChoice | None, typer.Option("--tracking-status")
    ] = None,
    package_status: Annotated[
        _PackageStatusChoice | None, typer.Option("--package-status")
    ] = None,
    page: Annotated[int, typer.Option("--page")] = 1,
) -> None:
    """List registered numbers."""
    api_key = _require_key(key)

    async def call() -> Any:
        async with Track17Client(api_key) as client:
            return await client.get_track_list(
                number_filter=number or None,
                tracking_status=(
                    TrackingStatus(tracking_status.value) if tracking_status else None
                ),
                package_status=MainStatus(package_status.value) if package_status else None,
                page_no=page,
            )

    result = _run(call())
    if as_json:
        _emit_json(result)
        return
    print(f"page {result.page_no}/{result.page_total} ({result.data_total} registrations total)")
    for item in result.items:
        print(f"  {_format_registered(item)}")


def _lifecycle(
    client_method: str,
    numbers: list[str],
    carrier: int | None,
    key: str | None,
    as_json: bool,
) -> None:
    api_key = _require_key(key)
    items = [NumberCarrier(number, carrier=carrier) for number in numbers]

    async def call() -> BatchResult[NumberCarrier]:
        async with Track17Client(api_key) as client:
            method = getattr(client, client_method)
            result: BatchResult[NumberCarrier] = await method(items)
            return result

    _print_batch(as_json, _run(call()), _format_number_carrier)


@app.command()
def stop(
    numbers: _NumbersArgument,
    key: _KeyOption = None,
    as_json: _JsonOption = False,
    carrier: _CarrierOption = None,
) -> None:
    """Stop tracking numbers."""
    _lifecycle("stop_track", numbers, carrier, key, as_json)


@app.command()
def retrack(
    numbers: _NumbersArgument,
    key: _KeyOption = None,
    as_json: _JsonOption = False,
    carrier: _CarrierOption = None,
) -> None:
    """Restart tracking for stopped numbers (once per number)."""
    _lifecycle("retrack", numbers, carrier, key, as_json)


@app.command()
def delete(
    numbers: _NumbersArgument,
    key: _KeyOption = None,
    as_json: _JsonOption = False,
    carrier: _CarrierOption = None,
) -> None:
    """Delete registrations (frees the slot)."""
    _lifecycle("delete_track", numbers, carrier, key, as_json)


@app.command("change-carrier")
def change_carrier(
    number: Annotated[str, typer.Argument()],
    old: Annotated[int, typer.Option("--old", help="current carrier code")],
    new: Annotated[int, typer.Option("--new", help="new carrier code")],
    key: _KeyOption = None,
    as_json: _JsonOption = False,
) -> None:
    """Reassign a number to another carrier."""
    api_key = _require_key(key)
    change = CarrierChange(number=number, carrier_old=old, carrier_new=new)

    async def call() -> BatchResult[NumberCarrier]:
        async with Track17Client(api_key) as client:
            return await client.change_carrier([change])

    _print_batch(as_json, _run(call()), _format_number_carrier)


@app.command("change-info")
def change_info(
    number: Annotated[str, typer.Argument()],
    tag: Annotated[
        str, typer.Option("--tag", help="new tag (the only field the API can change)")
    ],
    key: _KeyOption = None,
    as_json: _JsonOption = False,
    carrier: _CarrierOption = None,
) -> None:
    """Change a registration's tag."""
    api_key = _require_key(key)
    change = InfoChange(number=number, carrier=carrier, tag=tag)

    async def call() -> BatchResult[NumberCarrier]:
        async with Track17Client(api_key) as client:
            return await client.change_info([change])

    _print_batch(as_json, _run(call()), _format_number_carrier)


@app.command()
def carriers(
    search: Annotated[
        str | None, typer.Option("--search", help="substring match on carrier names")
    ] = None,
    code: Annotated[
        int | None, typer.Option("--code", help="look up the name for a carrier code")
    ] = None,
    name: Annotated[
        str | None, typer.Option("--name", help="look up the code for a carrier name")
    ] = None,
    refresh: Annotated[
        bool,
        typer.Option("--refresh", help="discard the cached carrier list and fetch a fresh copy"),
    ] = False,
    cache: Annotated[
        Path | None,
        typer.Option(
            "--cache",
            hidden=True,
            help="(deprecated) override the carrier-list cache path",
        ),
    ] = None,
    as_json: _JsonOption = False,
) -> None:
    """Search the carrier catalog.

    By default the carrier list is cached in the aio17track app directory
    and refetched automatically once the copy is older than 7 days.
    """
    if search is None and code is None and name is None:
        # Refuse to dump the multi-thousand-row catalog by accident.
        typer.echo("error: carriers requires one of --search, --code, or --name", err=True)
        raise typer.Exit(2)
    if cache is not None:
        typer.echo(
            "warning: --cache is deprecated; the carrier list is cached automatically "
            "(use --refresh to force a fresh fetch)",
            err=True,
        )

    async def _load(session: aiohttp.ClientSession, cache_path: Path | None) -> CarrierCatalog:
        if cache_path is not None and (refresh or _carrier_cache_is_stale(cache_path)):
            cache_path.unlink(missing_ok=True)
        catalog = CarrierCatalog(cache_path=cache_path)
        await catalog.load(session)
        return catalog

    async def call() -> CarrierCatalog:
        async with aiohttp.ClientSession() as session:
            if cache is not None:
                # An explicit path is a contract: failures stay usage errors.
                return await _load(session, cache)
            # The default cache is best-effort: an unusable app dir (read-only
            # home, containers) must never break a network-only lookup.
            try:
                default = _default_carrier_cache_path()
                default.parent.mkdir(parents=True, exist_ok=True)
                return await _load(session, default)
            except OSError as exc:
                typer.echo(
                    f"warning: carrier cache unavailable ({exc}); continuing without it",
                    err=True,
                )
                return await _load(session, None)

    catalog = _run(call())
    if code is not None:
        found_name = catalog.name(code)
        if found_name is None:
            typer.echo(f"no carrier with code {code}", err=True)
            raise typer.Exit(1)
        print(json.dumps({"code": code, "name": found_name}) if as_json else found_name)
        return
    if name is not None:
        found_code = catalog.code(name)
        if found_code is None:
            typer.echo(f"no carrier named {name!r}", err=True)
            raise typer.Exit(1)
        print(json.dumps({"code": found_code, "name": name}) if as_json else found_code)
        return
    assert search is not None
    matches = {
        carrier_code: carrier_name
        for carrier_code, carrier_name in catalog.all().items()
        if search.casefold() in carrier_name.casefold()
    }
    if as_json:
        _emit_json(matches)
        if not matches:
            raise typer.Exit(1)
        return
    for carrier_code, carrier_name in sorted(matches.items(), key=lambda pair: pair[1].casefold()):
        print(f"{carrier_code}\t{carrier_name}")
    if not matches:
        typer.echo(f"no carriers matching {search!r}", err=True)
        raise typer.Exit(1)


@app.command("webhook-verify")
def webhook_verify(
    sign: Annotated[str, typer.Option("--sign", help="value of the webhook's sign header")],
    body: Annotated[
        str, typer.Option("--body", help="path to the raw body ('-' for stdin)")
    ] = "-",
    key: _KeyOption = None,
    as_json: _JsonOption = False,
) -> None:
    """Verify a webhook signature over the raw body bytes."""
    api_key = _require_key(key)
    raw = _read_body(body)
    try:
        verify_signature(raw, sign, api_key)
    except SignatureError:
        if as_json:
            _emit_json({"valid": False})
        else:
            typer.echo("signature INVALID", err=True)
        raise typer.Exit(1) from None
    if as_json:
        _emit_json({"valid": True})
    else:
        print("signature ok")


@app.command("webhook-parse")
def webhook_parse(
    body: Annotated[
        str, typer.Option("--body", help="path to the raw body ('-' for stdin)")
    ] = "-",
    as_json: _JsonOption = False,
    events: _EventsOption = False,
) -> None:
    """Parse a webhook body into a typed event."""
    raw = _read_body(body)
    try:
        event = parse_event(raw)
    except Track17Error as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if as_json:
        _emit_json(event)
        return
    print(event.event)
    if isinstance(event.data, TrackInfo):
        print(_format_track_info(event.data, events=events))
    else:
        print(f"{event.data.number} (carrier {event.data.carrier}) tag={event.data.tag or '-'}")


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
