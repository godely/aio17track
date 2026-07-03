"""CLI: argument parsing, output shaping, exit codes (M7).

Each test drives cli.main() with argv against mocked HTTP; nothing here
touches the network or requires a key beyond the fake one passed in.
"""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from aioresponses import CallbackResult, aioresponses
from yarl import URL

from aio17track.carriers import _CARRIER_LIST_URL
from aio17track.cli import main

type FixtureLoader = Callable[[str], dict[str, Any]]

_BASE = "https://api.17track.net/track/v2.4"


def _echo_accepted(url: URL, **kwargs: Any) -> CallbackResult:
    sent = kwargs["json"]
    accepted = [{"number": item["number"], "carrier": item.get("carrier", 0)} for item in sent]
    return CallbackResult(payload={"code": 0, "data": {"accepted": accepted, "rejected": []}})


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEVENTEENTRACK_KEY", raising=False)
    monkeypatch.delenv("SEVENTEENTRACK_LIVE_KEY", raising=False)


# --- key resolution / usage errors ---


def test_missing_key_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["quota"])
    assert exit_code == 2
    assert "SEVENTEENTRACK_KEY" in capsys.readouterr().err


def test_key_from_environment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    load_fixture: FixtureLoader,
) -> None:
    monkeypatch.setenv("SEVENTEENTRACK_KEY", "env-key")
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/getquota", payload=load_fixture("getquota"))
        exit_code = main(["quota"])
        request_calls = mocked.requests[("POST", URL(f"{_BASE}/getquota"))]
        assert request_calls[0].kwargs["headers"]["17token"] == "env-key"
    assert exit_code == 0
    assert "remaining=1098" in capsys.readouterr().out


def test_live_key_env_var_is_not_accepted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI honors exactly SEVENTEENTRACK_KEY — the live-suite variable is
    not a fallback (owner decision on PR #9)."""
    monkeypatch.setenv("SEVENTEENTRACK_LIVE_KEY", "live-key")
    assert main(["quota"]) == 2
    assert "SEVENTEENTRACK_KEY" in capsys.readouterr().err


def test_unknown_command_exits_2() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["frobnicate"])
    assert excinfo.value.code == 2


# --- quota / info / register ---


def test_quota_json_output(
    capsys: pytest.CaptureFixture[str], load_fixture: FixtureLoader
) -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/getquota", payload=load_fixture("getquota"))
        exit_code = main(["quota", "--key", "k", "--json"])
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"remaining": 1098, "used": 2, "total": 1100}


def test_info_prints_status_and_events(
    capsys: pytest.CaptureFixture[str], load_fixture: FixtureLoader
) -> None:
    with aioresponses() as mocked:
        mocked.post(
            f"{_BASE}/gettrackinfo", payload=load_fixture("gettrackinfo_correios_2151")
        )
        exit_code = main(["info", "AA123456789BR", "--carrier", "2151", "--events", "--key", "k"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "accepted: AA123456789BR (carrier 2151)  Delivered / Delivered_Other" in out
    assert "Objeto postado" in out  # full history requested


def test_register_reports_rejections(
    capsys: pytest.CaptureFixture[str], load_fixture: FixtureLoader
) -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/register", payload=load_fixture("register_mixed"))
        exit_code = main(["register", "AA123456789BR", "1234", "--key", "k"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "accepted: AA123456789BR (carrier 2151)" in out
    assert "rejected: 1234  [-18010012] INVALID_DATA_FORMAT" in out


def test_register_passes_param_through_to_the_wire() -> None:
    """Carriers that require the extra param (phone/zip) work from the CLI."""
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/register", callback=_echo_accepted)
        exit_code = main(
            ["register", "LP00123456789", "--carrier", "190012", "--param", "90210", "--key", "k"]
        )
        sent = mocked.requests[("POST", URL(f"{_BASE}/register"))][0].kwargs["json"]
    assert exit_code == 0
    assert sent == [{"number": "LP00123456789", "carrier": 190012, "param": "90210"}]


def test_api_error_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/getquota", status=401)
        exit_code = main(["quota", "--key", "bad-key"])
    assert exit_code == 1
    assert "error:" in capsys.readouterr().err


# --- realtime guard ---


def test_realtime_standard_omits_cache_level() -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/getRealTimeTrackInfo", callback=_echo_accepted)
        exit_code = main(["realtime", "PKG0", "--carrier", "2151", "--key", "k"])
        sent = mocked.requests[("POST", URL(f"{_BASE}/getRealTimeTrackInfo"))][0].kwargs["json"]
    assert exit_code == 0
    assert sent == [{"number": "PKG0", "carrier": 2151}]


def test_realtime_instant_requires_explicit_flag() -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/getRealTimeTrackInfo", callback=_echo_accepted)
        exit_code = main(["realtime", "PKG0", "--instant", "--key", "k"])
        sent = mocked.requests[("POST", URL(f"{_BASE}/getRealTimeTrackInfo"))][0].kwargs["json"]
    assert exit_code == 0
    assert sent == [{"number": "PKG0", "cacheLevel": "Instant"}]


# --- list ---


def test_list_with_filters(
    capsys: pytest.CaptureFixture[str], load_fixture: FixtureLoader
) -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/gettracklist", payload=load_fixture("gettracklist_page"))
        exit_code = main(
            ["list", "--tracking-status", "Tracking", "--page", "1", "--key", "k"]
        )
        sent = mocked.requests[("POST", URL(f"{_BASE}/gettracklist"))][0].kwargs["json"]
    out = capsys.readouterr().out
    assert exit_code == 0
    assert sent == {"page_no": 1, "tracking_status": "Tracking"}
    assert "page 1/2 (43 registrations total)" in out
    assert "AA123456789BR (carrier 2151)  Tracking / Delivered" in out


# --- lifecycle / change ---


def test_delete_prints_accepted(capsys: pytest.CaptureFixture[str]) -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/deletetrack", callback=_echo_accepted)
        exit_code = main(["delete", "AA123456789BR", "--carrier", "2151", "--key", "k"])
    assert exit_code == 0
    assert "accepted: AA123456789BR (carrier 2151)" in capsys.readouterr().out


def test_change_carrier_payload() -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/changecarrier", callback=_echo_accepted)
        exit_code = main(
            ["change-carrier", "AA123456789BR", "--old", "2151", "--new", "190012", "--key", "k"]
        )
        sent = mocked.requests[("POST", URL(f"{_BASE}/changecarrier"))][0].kwargs["json"]
    assert exit_code == 0
    assert sent == [{"number": "AA123456789BR", "carrier_old": 2151, "carrier_new": 190012}]


def test_change_info_requires_tag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["change-info", "AA123456789BR", "--key", "k"])
    assert excinfo.value.code == 2


# --- carriers ---


def test_carriers_search(capsys: pytest.CaptureFixture[str]) -> None:
    sample = [
        {"key": 2151, "_name": "Correios"},
        {"key": 190012, "_name": "Yanwen"},
    ]
    with aioresponses() as mocked:
        mocked.get(_CARRIER_LIST_URL, payload=sample)
        exit_code = main(["carriers", "--search", "corr"])
    assert exit_code == 0
    assert "2151\tCorreios" in capsys.readouterr().out


def test_carriers_unknown_code_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    with aioresponses() as mocked:
        mocked.get(_CARRIER_LIST_URL, payload=[{"key": 2151, "_name": "Correios"}])
        exit_code = main(["carriers", "--code", "424242"])
    assert exit_code == 1
    assert "no carrier" in capsys.readouterr().err


def test_carriers_without_flags_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Refuses to dump the full multi-thousand-row catalog; no fetch happens."""
    with aioresponses() as mocked:
        exit_code = main(["carriers"])
        assert not mocked.requests  # rejected before any network
    assert exit_code == 2
    assert "--search" in capsys.readouterr().err


# --- webhook ---


def test_webhook_verify_and_parse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = (Path(__file__).parent / "fixtures" / "webhook_tracking_stopped.json").read_bytes()
    body_file = tmp_path / "body.json"
    body_file.write_bytes(body)
    sign = hashlib.sha256(body + b"/my-key").hexdigest()

    good = ["webhook-verify", "--sign", sign, "--body", str(body_file), "--key", "my-key"]
    assert main(good) == 0
    assert "signature ok" in capsys.readouterr().out

    bad = ["webhook-verify", "--sign", "0" * 64, "--body", str(body_file), "--key", "my-key"]
    assert main(bad) == 1
    assert "INVALID" in capsys.readouterr().err

    assert main(["webhook-parse", "--body", str(body_file)]) == 0
    out = capsys.readouterr().out
    assert "TRACKING_STOPPED" in out
    assert "AA123456789BR (carrier 2151)" in out


def test_webhook_verify_json_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--json holds for webhook-verify too: {"valid": bool}, same exit codes."""
    body = b'{"event":"TRACKING_STOPPED","data":{"number":"X","carrier":1}}'
    body_file = tmp_path / "body.json"
    body_file.write_bytes(body)
    sign = hashlib.sha256(body + b"/my-key").hexdigest()

    good = ["webhook-verify", "--sign", sign, "--body", str(body_file), "--key", "my-key"]
    assert main([*good, "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"valid": True}

    bad = ["webhook-verify", "--sign", "0" * 64, "--body", str(body_file), "--key", "my-key"]
    assert main([*bad, "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"valid": False}


def test_webhook_parse_malformed_body_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body_file = tmp_path / "bad.json"
    body_file.write_text("not json")
    assert main(["webhook-parse", "--body", str(body_file)]) == 1
    assert "error:" in capsys.readouterr().err
