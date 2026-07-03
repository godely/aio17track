"""Command-line interface — drive the client with no code (M7).

A thin layer over the public API: parse arguments, make the call, print.
Human-readable lines by default; ``--json`` (after the subcommand) emits
machine-readable output. The API key comes from ``--key`` or the
``SEVENTEENTRACK_KEY`` environment variable.

Exit codes: 0 success, 1 API/signature/lookup failure, 2 usage error.
"""

import argparse
import asyncio
import dataclasses
import json
import os
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

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


class _UsageError(Exception):
    """CLI-level usage problem (exit code 2)."""


def _resolve_key(args: argparse.Namespace) -> str:
    if args.key:
        return str(args.key)
    key = os.environ.get(_KEY_ENV_VAR)
    if key:
        return key
    raise _UsageError(
        f"no API key: pass --key or set {_KEY_ENV_VAR} in the environment"
    )


def _client(args: argparse.Namespace) -> Track17Client:
    return Track17Client(_resolve_key(args))


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
    args: argparse.Namespace, result: BatchResult[T], describe: Callable[[T], str]
) -> int:
    if args.json:
        _emit_json(result)
        return 0
    for item in result.accepted:
        print(f"accepted: {describe(item)}")
    for rejected in result.rejected:
        print(
            f"rejected: {rejected.number}  [{int(rejected.error_code)}] "
            f"{rejected.error_code.name}: {rejected.error_message}"
        )
    return 0


def _numbers(args: argparse.Namespace) -> list[NumberCarrier]:
    return [NumberCarrier(number, carrier=args.carrier) for number in args.numbers]


def _read_body(path: str) -> bytes:
    if path == "-":
        return sys.stdin.buffer.read()
    return Path(path).read_bytes()


# --- command handlers ---


async def _cmd_quota(args: argparse.Namespace) -> int:
    async with _client(args) as client:
        quota = await client.get_quota()
    if args.json:
        _emit_json(quota)
        return 0
    used = "?" if quota.used is None else quota.used
    total = "?" if quota.total is None else quota.total
    print(f"remaining={quota.remaining} used={used} total={total}")
    return 0


async def _cmd_register(args: argparse.Namespace) -> int:
    registrations = [
        TrackRegistration(
            number=number,
            carrier=args.carrier,
            tag=args.tag,
            order_no=args.order_no,
            lang=args.lang,
        )
        for number in args.numbers
    ]
    async with _client(args) as client:
        result = await client.register(registrations)
    return _print_batch(args, result, _format_registered)


async def _cmd_info(args: argparse.Namespace) -> int:
    async with _client(args) as client:
        result = await client.get_track_info(_numbers(args))
    return _print_batch(
        args, result, lambda info: _format_track_info(info, events=args.events)
    )


async def _cmd_realtime(args: argparse.Namespace) -> int:
    cache_level = CacheLevel.INSTANT if args.instant else CacheLevel.STANDARD
    async with _client(args) as client:
        result = await client.get_realtime_track_info(
            _numbers(args), cache_level=cache_level
        )
    return _print_batch(
        args, result, lambda info: _format_track_info(info, events=args.events)
    )


async def _cmd_list(args: argparse.Namespace) -> int:
    async with _client(args) as client:
        page = await client.get_track_list(
            number_filter=args.number or None,
            tracking_status=(
                TrackingStatus(args.tracking_status) if args.tracking_status else None
            ),
            package_status=MainStatus(args.package_status) if args.package_status else None,
            page_no=args.page,
        )
    if args.json:
        _emit_json(page)
        return 0
    print(f"page {page.page_no}/{page.page_total} ({page.data_total} registrations total)")
    for item in page.items:
        print(f"  {_format_registered(item)}")
    return 0


async def _cmd_lifecycle(args: argparse.Namespace) -> int:
    async with _client(args) as client:
        method = getattr(client, args.client_method)
        result: BatchResult[NumberCarrier] = await method(_numbers(args))
    return _print_batch(args, result, _format_number_carrier)


async def _cmd_change_carrier(args: argparse.Namespace) -> int:
    change = CarrierChange(number=args.number, carrier_old=args.old, carrier_new=args.new)
    async with _client(args) as client:
        result = await client.change_carrier([change])
    return _print_batch(args, result, _format_number_carrier)


async def _cmd_change_info(args: argparse.Namespace) -> int:
    change = InfoChange(number=args.number, carrier=args.carrier, tag=args.tag)
    async with _client(args) as client:
        result = await client.change_info([change])
    return _print_batch(args, result, _format_number_carrier)


async def _cmd_carriers(args: argparse.Namespace) -> int:
    if args.search is None and args.code is None and args.name is None:
        # Refuse to dump the multi-thousand-row catalog by accident.
        raise _UsageError("carriers requires one of --search, --code, or --name")
    catalog = CarrierCatalog(cache_path=args.cache)
    async with aiohttp.ClientSession() as session:
        await catalog.load(session)
    if args.code is not None:
        name = catalog.name(args.code)
        if name is None:
            print(f"no carrier with code {args.code}", file=sys.stderr)
            return 1
        print(json.dumps({"code": args.code, "name": name}) if args.json else name)
        return 0
    if args.name is not None:
        code = catalog.code(args.name)
        if code is None:
            print(f"no carrier named {args.name!r}", file=sys.stderr)
            return 1
        print(json.dumps({"code": code, "name": args.name}) if args.json else code)
        return 0
    matches = {
        code: name
        for code, name in catalog.all().items()
        if args.search.casefold() in name.casefold()
    }
    if args.json:
        _emit_json(matches)
        return 0 if matches else 1
    for code, name in sorted(matches.items(), key=lambda pair: pair[1].casefold()):
        print(f"{code}\t{name}")
    if not matches:
        print(f"no carriers matching {args.search!r}", file=sys.stderr)
        return 1
    return 0


async def _cmd_webhook_verify(args: argparse.Namespace) -> int:
    body = _read_body(args.body)
    try:
        verify_signature(body, args.sign, _resolve_key(args))
    except SignatureError:
        print("signature INVALID", file=sys.stderr)
        return 1
    print("signature ok")
    return 0


async def _cmd_webhook_parse(args: argparse.Namespace) -> int:
    event = parse_event(_read_body(args.body))
    if args.json:
        _emit_json(event)
        return 0
    print(event.event)
    if isinstance(event.data, TrackInfo):
        print(_format_track_info(event.data, events=args.events))
    else:
        print(f"{event.data.number} (carrier {event.data.carrier}) tag={event.data.tag or '-'}")
    return 0


# --- parser ---


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--key", help="17token API key (default: $SEVENTEENTRACK_KEY)")
    common.add_argument("--json", action="store_true", help="emit JSON output")

    parser = argparse.ArgumentParser(
        prog="aio17track",
        description="Command-line client for the 17TRACK Tracking API v2.4.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def command(
        name: str,
        handler: Callable[[argparse.Namespace], Any],
        help_text: str,
    ) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, parents=[common], help=help_text)
        sub.set_defaults(handler=handler)
        return sub

    sub = command("quota", _cmd_quota, "show the credit balance")

    sub = command("register", _cmd_register, "register numbers for tracking (1 credit each)")
    sub.add_argument("numbers", nargs="+")
    sub.add_argument("--carrier", type=int, help="carrier code (omit to auto-detect)")
    sub.add_argument("--tag")
    sub.add_argument("--order-no")
    sub.add_argument("--lang")

    sub = command("info", _cmd_info, "get tracking info for registered numbers")
    sub.add_argument("numbers", nargs="+")
    sub.add_argument("--carrier", type=int)
    sub.add_argument("--events", action="store_true", help="print the full event history")

    sub = command(
        "realtime",
        _cmd_realtime,
        "metered realtime lookup (1 credit per number; 10 with --instant)",
    )
    sub.add_argument("numbers", nargs="+")
    sub.add_argument("--carrier", type=int)
    sub.add_argument("--events", action="store_true")
    sub.add_argument(
        "--instant",
        action="store_true",
        help="INSTANT cache level: fresh carrier fetch, DEDUCTS 10 CREDITS PER NUMBER",
    )

    sub = command("list", _cmd_list, "list registered numbers")
    sub.add_argument("--number", action="append", help="filter by number (repeatable)")
    sub.add_argument(
        "--tracking-status",
        choices=sorted(m.value for m in TrackingStatus if not m.is_unknown),
    )
    sub.add_argument(
        "--package-status",
        choices=sorted(m.value for m in MainStatus if not m.is_unknown),
    )
    sub.add_argument("--page", type=int, default=1)

    for name, client_method, help_text in (
        ("stop", "stop_track", "stop tracking numbers"),
        ("retrack", "retrack", "restart tracking for stopped numbers (once per number)"),
        ("delete", "delete_track", "delete registrations (frees the slot)"),
    ):
        sub = command(name, _cmd_lifecycle, help_text)
        sub.set_defaults(client_method=client_method)
        sub.add_argument("numbers", nargs="+")
        sub.add_argument("--carrier", type=int)

    sub = command("change-carrier", _cmd_change_carrier, "reassign a number to another carrier")
    sub.add_argument("number")
    sub.add_argument("--old", type=int, required=True, help="current carrier code")
    sub.add_argument("--new", type=int, required=True, help="new carrier code")

    sub = command("change-info", _cmd_change_info, "change a registration's tag")
    sub.add_argument("number")
    sub.add_argument("--tag", required=True, help="new tag (the only field the API can change)")
    sub.add_argument("--carrier", type=int)

    sub = command("carriers", _cmd_carriers, "search the carrier catalog")
    sub.add_argument("--search", help="substring match on carrier names")
    sub.add_argument("--code", type=int, help="look up the name for a carrier code")
    sub.add_argument("--name", help="look up the code for a carrier name")
    sub.add_argument("--cache", type=Path, help="on-disk cache path for the carrier list")

    sub = command("webhook-verify", _cmd_webhook_verify, "verify a webhook signature")
    sub.add_argument("--sign", required=True, help="value of the webhook's sign header")
    sub.add_argument("--body", default="-", help="path to the raw body ('-' for stdin)")

    sub = command("webhook-parse", _cmd_webhook_parse, "parse a webhook body")
    sub.add_argument("--body", default="-", help="path to the raw body ('-' for stdin)")
    sub.add_argument("--events", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return int(asyncio.run(args.handler(args)))
    except _UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:  # client-side guards (filter caps, cache level, ...)
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:  # unreadable --body / --cache paths
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Track17Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
