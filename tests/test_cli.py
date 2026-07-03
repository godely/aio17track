"""CLI: argument parsing, output shaping, exit codes (Typer-based).

Each test drives the Typer app through CliRunner against mocked HTTP;
nothing here touches the network or requires a key beyond the fake one
passed in.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from aioresponses import CallbackResult, aioresponses
from typer.testing import CliRunner
from yarl import URL

from aio17track.carriers import _CARRIER_LIST_URL
from aio17track.cli import app

_BASE = "https://api.17track.net/track/v2.4"

runner = CliRunner()


def _echo_accepted(url: URL, **kwargs: Any) -> CallbackResult:
    sent = kwargs["json"]
    accepted = [{"number": item["number"], "carrier": item.get("carrier", 0)} for item in sent]
    return CallbackResult(payload={"code": 0, "data": {"accepted": accepted, "rejected": []}})


def _sent_json(mocked: aioresponses, endpoint: str, index: int = 0) -> Any:
    return mocked.requests[("POST", URL(f"{_BASE}/{endpoint}"))][index].kwargs["json"]


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEVENTEENTRACK_KEY", raising=False)


# --- key resolution / usage errors ---


def test_missing_key_is_a_usage_error() -> None:
    result = runner.invoke(app, ["quota"])
    assert result.exit_code == 2
    assert "SEVENTEENTRACK_KEY" in result.stderr


def test_key_from_environment(
    monkeypatch: pytest.MonkeyPatch, load_fixture: Any
) -> None:
    monkeypatch.setenv("SEVENTEENTRACK_KEY", "env-key")
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/getquota", payload=load_fixture("getquota"))
        result = runner.invoke(app, ["quota"])
        request_calls = mocked.requests[("POST", URL(f"{_BASE}/getquota"))]
        assert request_calls[0].kwargs["headers"]["17token"] == "env-key"
    assert result.exit_code == 0
    assert "remaining=1098" in result.stdout


def test_unknown_command_exits_2() -> None:
    result = runner.invoke(app, ["frobnicate"])
    assert result.exit_code == 2


# --- quota / info / register ---


def test_quota_json_output(load_fixture: Any) -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/getquota", payload=load_fixture("getquota"))
        result = runner.invoke(app, ["quota", "--key", "k", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"remaining": 1098, "used": 2, "total": 1100}


def test_info_prints_status_and_events(load_fixture: Any) -> None:
    with aioresponses() as mocked:
        mocked.post(
            f"{_BASE}/gettrackinfo", payload=load_fixture("gettrackinfo_correios_2151")
        )
        result = runner.invoke(
            app, ["info", "AA123456789BR", "--carrier", "2151", "--events", "--key", "k"]
        )
    assert result.exit_code == 0
    assert "accepted: AA123456789BR (carrier 2151)  Delivered / Delivered_Other" in result.stdout
    assert "Objeto postado" in result.stdout  # full history requested


def test_register_reports_rejections(load_fixture: Any) -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/register", payload=load_fixture("register_mixed"))
        result = runner.invoke(app, ["register", "AA123456789BR", "1234", "--key", "k"])
    assert result.exit_code == 0
    assert "accepted: AA123456789BR (carrier 2151)" in result.stdout
    assert "rejected: 1234  [-18010012] INVALID_DATA_FORMAT" in result.stdout


def test_register_passes_param_through_to_the_wire() -> None:
    """Carriers that require the extra param (phone/zip) work from the CLI."""
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/register", callback=_echo_accepted)
        result = runner.invoke(
            app,
            ["register", "LP00123456789", "--carrier", "190012", "--param", "90210", "--key", "k"],
        )
        sent = _sent_json(mocked, "register")
    assert result.exit_code == 0
    assert sent == [{"number": "LP00123456789", "carrier": 190012, "param": "90210"}]


def test_api_error_exits_1() -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/getquota", status=401)
        result = runner.invoke(app, ["quota", "--key", "bad-key"])
    assert result.exit_code == 1
    assert "error:" in result.stderr


# --- realtime guard ---


def test_realtime_standard_omits_cache_level() -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/getRealTimeTrackInfo", callback=_echo_accepted)
        result = runner.invoke(app, ["realtime", "PKG0", "--carrier", "2151", "--key", "k"])
        sent = _sent_json(mocked, "getRealTimeTrackInfo")
    assert result.exit_code == 0
    assert sent == [{"number": "PKG0", "carrier": 2151}]


def test_realtime_instant_requires_explicit_flag() -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/getRealTimeTrackInfo", callback=_echo_accepted)
        result = runner.invoke(app, ["realtime", "PKG0", "--instant", "--key", "k"])
        sent = _sent_json(mocked, "getRealTimeTrackInfo")
    assert result.exit_code == 0
    assert sent == [{"number": "PKG0", "cacheLevel": "Instant"}]


# --- list ---


def test_list_with_filters(load_fixture: Any) -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/gettracklist", payload=load_fixture("gettracklist_page"))
        result = runner.invoke(
            app, ["list", "--tracking-status", "Tracking", "--page", "1", "--key", "k"]
        )
        sent = _sent_json(mocked, "gettracklist")
    assert result.exit_code == 0
    assert sent == {"page_no": 1, "tracking_status": "Tracking"}
    assert "page 1/2 (43 registrations total)" in result.stdout
    assert "AA123456789BR (carrier 2151)  Tracking / Delivered" in result.stdout


def test_list_rejects_invalid_status_choice() -> None:
    result = runner.invoke(app, ["list", "--tracking-status", "Sideways", "--key", "k"])
    assert result.exit_code == 2


def test_cli_choice_enums_match_the_library_tables() -> None:
    """The CLI-facing choice enums must track the library enums (minus UNKNOWN)."""
    from aio17track import MainStatus, TrackingStatus
    from aio17track.cli import _PackageStatusChoice, _TrackingStatusChoice

    assert {choice.value for choice in _TrackingStatusChoice} == {
        member.value for member in TrackingStatus if not member.is_unknown
    }
    assert {choice.value for choice in _PackageStatusChoice} == {
        member.value for member in MainStatus if not member.is_unknown
    }


# --- lifecycle / change ---


def test_delete_prints_accepted() -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/deletetrack", callback=_echo_accepted)
        result = runner.invoke(
            app, ["delete", "AA123456789BR", "--carrier", "2151", "--key", "k"]
        )
    assert result.exit_code == 0
    assert "accepted: AA123456789BR (carrier 2151)" in result.stdout


def test_change_carrier_payload() -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/changecarrier", callback=_echo_accepted)
        result = runner.invoke(
            app,
            [
                "change-carrier",
                "AA123456789BR",
                "--old",
                "2151",
                "--new",
                "190012",
                "--key",
                "k",
            ],
        )
        sent = _sent_json(mocked, "changecarrier")
    assert result.exit_code == 0
    assert sent == [{"number": "AA123456789BR", "carrier_old": 2151, "carrier_new": 190012}]


def test_change_info_requires_tag() -> None:
    result = runner.invoke(app, ["change-info", "AA123456789BR", "--key", "k"])
    assert result.exit_code == 2


# --- carriers ---


def test_carriers_search() -> None:
    sample = [
        {"key": 2151, "_name": "Correios"},
        {"key": 190012, "_name": "Yanwen"},
    ]
    with aioresponses() as mocked:
        mocked.get(_CARRIER_LIST_URL, payload=sample)
        result = runner.invoke(app, ["carriers", "--search", "corr"])
    assert result.exit_code == 0
    assert "2151\tCorreios" in result.stdout


def test_carriers_unknown_code_exits_1() -> None:
    with aioresponses() as mocked:
        mocked.get(_CARRIER_LIST_URL, payload=[{"key": 2151, "_name": "Correios"}])
        result = runner.invoke(app, ["carriers", "--code", "424242"])
    assert result.exit_code == 1
    assert "no carrier" in result.stderr


def test_carriers_without_flags_is_a_usage_error() -> None:
    """Refuses to dump the full multi-thousand-row catalog; no fetch happens."""
    with aioresponses() as mocked:
        result = runner.invoke(app, ["carriers"])
        assert not mocked.requests  # rejected before any network
    assert result.exit_code == 2
    assert "--search" in result.stderr


# --- webhook ---


def test_webhook_verify_and_parse(tmp_path: Path) -> None:
    body = (Path(__file__).parent / "fixtures" / "webhook_tracking_stopped.json").read_bytes()
    body_file = tmp_path / "body.json"
    body_file.write_bytes(body)
    sign = hashlib.sha256(body + b"/my-key").hexdigest()

    good = ["webhook-verify", "--sign", sign, "--body", str(body_file), "--key", "my-key"]
    result = runner.invoke(app, good)
    assert result.exit_code == 0
    assert "signature ok" in result.stdout

    bad = ["webhook-verify", "--sign", "0" * 64, "--body", str(body_file), "--key", "my-key"]
    result = runner.invoke(app, bad)
    assert result.exit_code == 1
    assert "INVALID" in result.stderr

    result = runner.invoke(app, ["webhook-parse", "--body", str(body_file)])
    assert result.exit_code == 0
    assert "TRACKING_STOPPED" in result.stdout
    assert "AA123456789BR (carrier 2151)" in result.stdout


def test_webhook_verify_json_output(tmp_path: Path) -> None:
    """--json holds for webhook-verify too: {"valid": bool}, same exit codes."""
    body = b'{"event":"TRACKING_STOPPED","data":{"number":"X","carrier":1}}'
    body_file = tmp_path / "body.json"
    body_file.write_bytes(body)
    sign = hashlib.sha256(body + b"/my-key").hexdigest()

    good = ["webhook-verify", "--sign", sign, "--body", str(body_file), "--key", "my-key"]
    result = runner.invoke(app, [*good, "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"valid": True}

    bad = ["webhook-verify", "--sign", "0" * 64, "--body", str(body_file), "--key", "my-key"]
    result = runner.invoke(app, [*bad, "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {"valid": False}


def test_webhook_parse_malformed_body_exits_1(tmp_path: Path) -> None:
    body_file = tmp_path / "bad.json"
    body_file.write_text("not json")
    result = runner.invoke(app, ["webhook-parse", "--body", str(body_file)])
    assert result.exit_code == 1
    assert "error:" in result.stderr
