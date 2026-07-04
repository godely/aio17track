"""CLI: argument parsing, output shaping, exit codes (Typer-based).

Each test drives the Typer app through CliRunner against mocked HTTP;
nothing here touches the network or requires a key beyond the fake one
passed in.
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
import typer
from aioresponses import CallbackResult, aioresponses
from typer.testing import CliRunner
from yarl import URL

from aio17track import cli
from aio17track.carriers import _CARRIER_LIST_URL
from aio17track.cli import app

_BASE = "https://api.17track.net/track/v2.4"

# Captured before the autouse fixtures below patch them, so the real
# app-dir derivations stay testable.
_real_default_carrier_cache_path = cli._default_carrier_cache_path
_real_stored_key_path = cli._stored_key_path

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


@pytest.fixture(autouse=True)
def default_cache_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the carriers default cache into tmp_path so no test ever
    touches the real per-user app directory."""
    path = tmp_path / "app-dir" / "carriers.json"
    monkeypatch.setattr(cli, "_default_carrier_cache_path", lambda: path)
    return path


@pytest.fixture(autouse=True)
def stored_key_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the stored-key file into tmp_path so tests never read a real
    key saved by `auth login` on the developer's machine."""
    path = tmp_path / "app-dir" / "api-key"
    monkeypatch.setattr(cli, "_stored_key_path", lambda: path)
    return path


# --- key resolution / usage errors ---


def test_missing_key_is_a_usage_error() -> None:
    result = runner.invoke(app, ["quota"])
    assert result.exit_code == 2
    assert "SEVENTEENTRACK_KEY" in result.stderr
    assert "auth login" in result.stderr


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


# --- auth ---


def test_auth_login_verifies_and_stores_the_key(
    stored_key_path: Path, load_fixture: Any
) -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/getquota", payload=load_fixture("getquota"))
        result = runner.invoke(app, ["auth", "login", "--key", "sk-fresh"])
        headers = mocked.requests[("POST", URL(f"{_BASE}/getquota"))][0].kwargs["headers"]
    assert result.exit_code == 0
    assert headers["17token"] == "sk-fresh"  # verified against the API before storing
    assert stored_key_path.read_text() == "sk-fresh\n"
    assert stored_key_path.stat().st_mode & 0o777 == 0o600
    assert "saved" in result.stdout


def test_auth_login_prompts_when_key_omitted(
    stored_key_path: Path, load_fixture: Any
) -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/getquota", payload=load_fixture("getquota"))
        result = runner.invoke(app, ["auth", "login"], input="sk-typed\n")
    assert result.exit_code == 0
    assert stored_key_path.read_text() == "sk-typed\n"
    assert "sk-typed" not in result.stdout  # hidden input never echoes


def test_auth_login_tightens_a_preexisting_loose_key_file(
    stored_key_path: Path, load_fixture: Any
) -> None:
    """Re-login over a key file with loose permissions must end at 0600."""
    stored_key_path.parent.mkdir(parents=True)
    stored_key_path.write_text("sk-old\n")
    stored_key_path.chmod(0o644)
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/getquota", payload=load_fixture("getquota"))
        result = runner.invoke(app, ["auth", "login", "--key", "sk-fresh"])
    assert result.exit_code == 0
    assert stored_key_path.read_text() == "sk-fresh\n"
    assert stored_key_path.stat().st_mode & 0o777 == 0o600


def test_auth_login_rejected_key_is_not_stored(stored_key_path: Path) -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/getquota", status=401)
        result = runner.invoke(app, ["auth", "login", "--key", "sk-bad"])
    assert result.exit_code == 1
    assert not stored_key_path.exists()


def test_stored_key_is_used_when_no_flag_or_env(
    stored_key_path: Path, load_fixture: Any
) -> None:
    stored_key_path.parent.mkdir(parents=True)
    stored_key_path.write_text("sk-stored\n")
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/getquota", payload=load_fixture("getquota"))
        result = runner.invoke(app, ["quota"])
        headers = mocked.requests[("POST", URL(f"{_BASE}/getquota"))][0].kwargs["headers"]
    assert result.exit_code == 0
    assert headers["17token"] == "sk-stored"


def test_env_var_beats_stored_key(
    monkeypatch: pytest.MonkeyPatch, stored_key_path: Path, load_fixture: Any
) -> None:
    stored_key_path.parent.mkdir(parents=True)
    stored_key_path.write_text("sk-stored\n")
    monkeypatch.setenv("SEVENTEENTRACK_KEY", "env-key")
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/getquota", payload=load_fixture("getquota"))
        result = runner.invoke(app, ["quota"])
        headers = mocked.requests[("POST", URL(f"{_BASE}/getquota"))][0].kwargs["headers"]
    assert result.exit_code == 0
    assert headers["17token"] == "env-key"


def test_auth_status_without_any_key_exits_1() -> None:
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 1
    assert "auth login" in result.stderr


def test_auth_status_masks_the_stored_key(stored_key_path: Path) -> None:
    stored_key_path.parent.mkdir(parents=True)
    stored_key_path.write_text("0123456789abcdef\n")
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    assert "0123456789abcdef" not in result.stdout
    assert "0123…cdef" in result.stdout
    assert str(stored_key_path) in result.stdout


def test_auth_status_json_reports_the_source(
    monkeypatch: pytest.MonkeyPatch, stored_key_path: Path
) -> None:
    stored_key_path.parent.mkdir(parents=True)
    stored_key_path.write_text("sk-stored\n")
    monkeypatch.setenv("SEVENTEENTRACK_KEY", "env-key")
    result = runner.invoke(app, ["auth", "status", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "source": "environment",
        "environment": True,
        "stored": True,
        "path": str(stored_key_path),
    }


def test_auth_logout_removes_the_stored_key(stored_key_path: Path) -> None:
    stored_key_path.parent.mkdir(parents=True)
    stored_key_path.write_text("sk-stored\n")
    result = runner.invoke(app, ["auth", "logout"])
    assert result.exit_code == 0
    assert not stored_key_path.exists()

    again = runner.invoke(app, ["auth", "logout"])  # idempotent
    assert again.exit_code == 0
    assert "no stored key" in again.stdout


def test_stored_key_path_lives_in_the_app_dir() -> None:
    expected = Path(typer.get_app_dir("aio17track")) / "api-key"
    assert _real_stored_key_path() == expected


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

_CARRIER_SAMPLE = [
    {"key": 2151, "_name": "Correios"},
    {"key": 190012, "_name": "Yanwen"},
]


def test_carriers_search() -> None:
    with aioresponses() as mocked:
        mocked.get(_CARRIER_LIST_URL, payload=_CARRIER_SAMPLE)
        result = runner.invoke(app, ["carriers", "--search", "corr"])
    assert result.exit_code == 0
    assert "2151\tCorreios" in result.stdout


def test_carriers_unknown_code_exits_1() -> None:
    with aioresponses() as mocked:
        mocked.get(_CARRIER_LIST_URL, payload=[{"key": 2151, "_name": "Correios"}])
        result = runner.invoke(app, ["carriers", "--code", "424242"])
    assert result.exit_code == 1
    assert "no carrier" in result.stderr


def test_carriers_caches_by_default(default_cache_path: Path) -> None:
    """The first run creates the app-dir cache; the second is served from it
    without touching the network."""
    with aioresponses() as mocked:
        mocked.get(_CARRIER_LIST_URL, payload=_CARRIER_SAMPLE)
        first = runner.invoke(app, ["carriers", "--search", "corr"])
        second = runner.invoke(app, ["carriers", "--search", "corr"])
        fetches = mocked.requests.get(("GET", URL(_CARRIER_LIST_URL)), [])
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "2151\tCorreios" in second.stdout
    assert len(fetches) == 1
    assert json.loads(default_cache_path.read_text()) == _CARRIER_SAMPLE


def test_carriers_fresh_cache_skips_the_fetch(default_cache_path: Path) -> None:
    default_cache_path.parent.mkdir(parents=True)
    default_cache_path.write_text(json.dumps(_CARRIER_SAMPLE))
    with aioresponses() as mocked:  # no GET registered: any fetch would fail
        result = runner.invoke(app, ["carriers", "--code", "2151"])
        assert not mocked.requests
    assert result.exit_code == 0
    assert "Correios" in result.stdout


def test_carriers_stale_cache_is_refetched(default_cache_path: Path) -> None:
    """A cache older than 7 days is discarded and replaced by a fresh fetch."""
    default_cache_path.parent.mkdir(parents=True)
    default_cache_path.write_text(json.dumps([{"key": 2151, "_name": "Stale Name"}]))
    week_ago = time.time() - 8 * 24 * 60 * 60
    os.utime(default_cache_path, (week_ago, week_ago))
    with aioresponses() as mocked:
        mocked.get(_CARRIER_LIST_URL, payload=_CARRIER_SAMPLE)
        result = runner.invoke(app, ["carriers", "--code", "2151"])
    assert result.exit_code == 0
    assert "Correios" in result.stdout
    assert json.loads(default_cache_path.read_text()) == _CARRIER_SAMPLE


def test_carriers_refresh_forces_a_fetch(default_cache_path: Path) -> None:
    """--refresh bypasses a perfectly fresh cache."""
    default_cache_path.parent.mkdir(parents=True)
    default_cache_path.write_text(json.dumps([{"key": 2151, "_name": "Old Name"}]))
    with aioresponses() as mocked:
        mocked.get(_CARRIER_LIST_URL, payload=_CARRIER_SAMPLE)
        result = runner.invoke(app, ["carriers", "--code", "2151", "--refresh"])
    assert result.exit_code == 0
    assert "Correios" in result.stdout
    assert json.loads(default_cache_path.read_text()) == _CARRIER_SAMPLE


@pytest.mark.parametrize("blocker", ["file-parent", "dir-as-cache"])
def test_carriers_unusable_default_cache_is_best_effort(
    default_cache_path: Path, blocker: str
) -> None:
    """A broken app dir must never break a network-only lookup: the lookup
    still succeeds (with a warning), it just runs uncached. Only an explicit
    --cache path is a hard usage error."""
    if blocker == "file-parent":
        default_cache_path.parent.parent.mkdir(parents=True, exist_ok=True)
        default_cache_path.parent.write_text("not a directory")  # mkdir fails
    else:
        default_cache_path.mkdir(parents=True)  # cache read/write fails
    with aioresponses() as mocked:
        mocked.get(_CARRIER_LIST_URL, payload=_CARRIER_SAMPLE)
        result = runner.invoke(app, ["carriers", "--code", "2151"])
    assert result.exit_code == 0
    assert "Correios" in result.stdout
    assert "warning: carrier cache unavailable" in result.stderr


def test_carriers_default_cache_path_lives_in_the_app_dir() -> None:
    expected = Path(typer.get_app_dir("aio17track")) / "carriers.json"
    assert _real_default_carrier_cache_path() == expected


def test_carriers_cache_override_is_deprecated(tmp_path: Path) -> None:
    """--cache still works (shipped in the Typer rework) but warns."""
    override = tmp_path / "override.json"
    with aioresponses() as mocked:
        mocked.get(_CARRIER_LIST_URL, payload=_CARRIER_SAMPLE)
        result = runner.invoke(app, ["carriers", "--search", "corr", "--cache", str(override)])
    assert result.exit_code == 0
    assert "deprecated" in result.stderr
    assert json.loads(override.read_text()) == _CARRIER_SAMPLE


def test_carriers_unusable_cache_path_is_a_usage_error(tmp_path: Path) -> None:
    """--cache pointing at a directory maps to exit 2, not a traceback
    (parity with the argparse CLI's OSError handling)."""
    with aioresponses() as mocked:
        mocked.get(_CARRIER_LIST_URL, payload=[{"key": 2151, "_name": "Correios"}])
        result = runner.invoke(
            app, ["carriers", "--search", "corr", "--cache", str(tmp_path)]
        )
    assert result.exit_code == 2
    assert "error:" in result.stderr


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
