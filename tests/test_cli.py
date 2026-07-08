"""CLI: argument parsing, output shaping, exit codes (Typer-based).

Each test drives the Typer app through CliRunner against mocked HTTP;
nothing here touches the network or requires a key beyond the fake one
passed in.
"""

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


def test_status_prints_latest_only(load_fixture: Any) -> None:
    """`status` is latest-state by definition — no flag brings the history."""
    with aioresponses() as mocked:
        mocked.post(
            f"{_BASE}/gettrackinfo", payload=load_fixture("gettrackinfo_correios_2151")
        )
        result = runner.invoke(app, ["status", "AA123456789BR", "--key", "k"])
    assert result.exit_code == 0
    assert "accepted: AA123456789BR (carrier 2151)  Delivered / Delivered_Other" in result.stdout
    assert "latest:" in result.stdout
    assert "Objeto postado" not in result.stdout  # history stays out


def test_events_prints_full_history(load_fixture: Any) -> None:
    with aioresponses() as mocked:
        mocked.post(
            f"{_BASE}/gettrackinfo", payload=load_fixture("gettrackinfo_correios_2151")
        )
        result = runner.invoke(app, ["events", "AA123456789BR", "--key", "k"])
    assert result.exit_code == 0
    assert "accepted: AA123456789BR (carrier 2151)  Delivered / Delivered_Other" in result.stdout
    assert "Objeto postado" in result.stdout


def test_status_json_omits_events(load_fixture: Any) -> None:
    """The JSON must agree with the human output about what `status` means."""
    with aioresponses() as mocked:
        mocked.post(
            f"{_BASE}/gettrackinfo", payload=load_fixture("gettrackinfo_correios_2151")
        )
        result = runner.invoke(app, ["status", "AA123456789BR", "--key", "k", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["accepted"], "fixture must yield an accepted item"
    for item in payload["accepted"]:
        assert "events" not in item
        assert item["latest_event"] is not None


def test_events_json_includes_events(load_fixture: Any) -> None:
    with aioresponses() as mocked:
        mocked.post(
            f"{_BASE}/gettrackinfo", payload=load_fixture("gettrackinfo_correios_2151")
        )
        result = runner.invoke(app, ["events", "AA123456789BR", "--key", "k", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert all(item["events"] for item in payload["accepted"])


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


# --- removed commands stay removed ---


def test_info_and_realtime_are_not_exposed() -> None:
    """`info` was split into `status`/`events`; `realtime` (the metered,
    freemium-tier lookup) is library-only until a post-release decision.
    Neither may creep back."""
    help_result = runner.invoke(app, ["--help"])
    assert "realtime" not in help_result.stdout
    assert runner.invoke(app, ["info", "AA123456789BR", "--key", "k"]).exit_code == 2
    assert runner.invoke(app, ["realtime", "AA123456789BR", "--key", "k"]).exit_code == 2


def test_change_commands_are_merged_into_update() -> None:
    """`change-carrier` and `change-info` were merged into `update`;
    neither may creep back."""
    help_result = runner.invoke(app, ["--help"])
    assert "change-carrier" not in help_result.stdout
    assert "change-info" not in help_result.stdout
    change_carrier = ["change-carrier", "AA123456789BR", "--new", "190012", "--key", "k"]
    assert runner.invoke(app, change_carrier).exit_code == 2
    change_info = ["change-info", "AA123456789BR", "--tag", "t", "--key", "k"]
    assert runner.invoke(app, change_info).exit_code == 2


# --- list ---


def test_list_with_filters(load_fixture: Any) -> None:
    payload = load_fixture("gettracklist_page")
    payload["page"].update(page_total=1, data_total=2)  # single page: no follow-up fetch
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/gettracklist", payload=payload)
        result = runner.invoke(app, ["list", "--tracking-status", "Tracking", "--key", "k"])
        sent = _sent_json(mocked, "gettracklist")
    assert result.exit_code == 0
    assert sent == {"page_no": 1, "tracking_status": "Tracking"}
    assert "2 registrations" in result.stdout
    assert "AA123456789BR (carrier 2151)  Tracking / Delivered" in result.stdout


def test_list_fetches_every_page(load_fixture: Any) -> None:
    """`list` walks gettracklist to the last page; --page is not a CLI concern."""
    page_one = load_fixture("gettracklist_page")  # reports page 1/2
    page_two = load_fixture("gettracklist_page")
    page_two["page"].update(page_no=2)
    page_two["data"]["accepted"] = [
        dict(page_two["data"]["accepted"][0], number="BB987654321BR")
    ]
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/gettracklist", payload=page_one)
        mocked.post(f"{_BASE}/gettracklist", payload=page_two)
        result = runner.invoke(app, ["list", "--key", "k"])
        calls = mocked.requests[("POST", URL(f"{_BASE}/gettracklist"))]
    assert result.exit_code == 0
    assert [call.kwargs["json"]["page_no"] for call in calls] == [1, 2]
    assert "3 registrations" in result.stdout
    assert "AA123456789BR" in result.stdout
    assert "BB987654321BR" in result.stdout


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


# --- lifecycle / update ---


def test_delete_prints_accepted() -> None:
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/deletetrack", callback=_echo_accepted)
        result = runner.invoke(app, ["delete", "AA123456789BR", "--key", "k"])
        sent = _sent_json(mocked, "deletetrack")
    assert result.exit_code == 0
    assert sent == [{"number": "AA123456789BR"}]  # the account resolves the carrier
    assert "accepted: AA123456789BR" in result.stdout


@pytest.mark.parametrize(
    "command",
    [
        ["status", "AA123456789BR"],
        ["events", "AA123456789BR"],
        ["stop", "AA123456789BR"],
        ["retrack", "AA123456789BR"],
        ["delete", "AA123456789BR"],
    ],
)
def test_registration_scoped_commands_reject_carrier(command: list[str]) -> None:
    """--carrier stays only where the number alone is ambiguous (register,
    realtime); on registration-scoped commands it could only be redundant
    or wrong, so it was removed and must not creep back."""
    result = runner.invoke(app, [*command, "--carrier", "2151", "--key", "k"])
    assert result.exit_code == 2


def test_update_carrier_resolves_the_current_carrier(load_fixture: Any) -> None:
    """--carrier is the new code; the CLI looks up carrier_old itself."""
    payload = load_fixture("gettracklist_page")
    payload["page"].update(page_total=1, data_total=1)  # single page: no follow-up fetch
    payload["data"]["accepted"] = [payload["data"]["accepted"][0]]  # AA... on carrier 2151
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/gettracklist", payload=payload)
        mocked.post(f"{_BASE}/changecarrier", callback=_echo_accepted)
        result = runner.invoke(
            app, ["update", "AA123456789BR", "--carrier", "190012", "--key", "k"]
        )
        looked_up = _sent_json(mocked, "gettracklist")
        sent = _sent_json(mocked, "changecarrier")
        assert ("POST", URL(f"{_BASE}/changeinfo")) not in mocked.requests  # no tag change
    assert result.exit_code == 0
    assert looked_up == {"page_no": 1, "number": "AA123456789BR"}
    assert sent == [{"number": "AA123456789BR", "carrier_old": 2151, "carrier_new": 190012}]


def test_update_tag_needs_no_lookup() -> None:
    """--tag alone goes straight to changeinfo; the carrier lookup exists
    only to fill changecarrier's carrier_old."""
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/changeinfo", callback=_echo_accepted)
        result = runner.invoke(app, ["update", "AA123456789BR", "--tag", "new-tag", "--key", "k"])
        sent = _sent_json(mocked, "changeinfo")
        assert ("POST", URL(f"{_BASE}/gettracklist")) not in mocked.requests
        assert ("POST", URL(f"{_BASE}/changecarrier")) not in mocked.requests
    assert result.exit_code == 0
    assert sent == [{"number": "AA123456789BR", "items": {"tag": "new-tag"}}]
    assert "tag: accepted:" in result.stdout


def test_update_carrier_and_tag_hits_both_endpoints(load_fixture: Any) -> None:
    payload = load_fixture("gettracklist_page")
    payload["page"].update(page_total=1, data_total=1)  # single page: no follow-up fetch
    payload["data"]["accepted"] = [payload["data"]["accepted"][0]]  # AA... on carrier 2151
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/gettracklist", payload=payload)
        mocked.post(f"{_BASE}/changecarrier", callback=_echo_accepted)
        mocked.post(f"{_BASE}/changeinfo", callback=_echo_accepted)
        result = runner.invoke(
            app,
            ["update", "AA123456789BR", "--carrier", "190012", "--tag", "t", "--key", "k"],
        )
        lookups = mocked.requests[("POST", URL(f"{_BASE}/gettracklist"))]
        carrier_sent = _sent_json(mocked, "changecarrier")
        tag_sent = _sent_json(mocked, "changeinfo")
    assert result.exit_code == 0
    assert len(lookups) == 1
    assert carrier_sent == [{"number": "AA123456789BR", "carrier_old": 2151, "carrier_new": 190012}]
    assert tag_sent == [{"number": "AA123456789BR", "items": {"tag": "t"}}]
    assert "carrier: accepted:" in result.stdout
    assert "tag: accepted:" in result.stdout


def test_update_ambiguous_number_aborts_before_mutating(load_fixture: Any) -> None:
    """A number registered under several carriers exits 2 listing the codes,
    before either endpoint is touched — even the tag change is withheld."""
    payload = load_fixture("gettracklist_page")
    payload["page"].update(page_total=1, data_total=2)  # single page: no follow-up fetch
    row = payload["data"]["accepted"][0]
    payload["data"]["accepted"] = [dict(row, carrier=2151), dict(row, carrier=190012)]
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/gettracklist", payload=payload)
        result = runner.invoke(
            app,
            ["update", "AA123456789BR", "--carrier", "100003", "--tag", "t", "--key", "k"],
        )
        assert ("POST", URL(f"{_BASE}/changecarrier")) not in mocked.requests
        assert ("POST", URL(f"{_BASE}/changeinfo")) not in mocked.requests
    assert result.exit_code == 2
    assert "2151" in result.stderr
    assert "190012" in result.stderr


def test_update_carrier_lookup_walks_every_page(load_fixture: Any) -> None:
    """A duplicate registration on a later listing page still aborts: the
    lookup pages through the filtered results before trusting a single match."""
    page_one = load_fixture("gettracklist_page")  # reports page 1/2
    page_one["data"]["accepted"] = [dict(page_one["data"]["accepted"][0], carrier=2151)]
    page_two = load_fixture("gettracklist_page")
    page_two["page"].update(page_no=2)
    page_two["data"]["accepted"] = [dict(page_two["data"]["accepted"][0], carrier=190012)]
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/gettracklist", payload=page_one)
        mocked.post(f"{_BASE}/gettracklist", payload=page_two)
        result = runner.invoke(
            app, ["update", "AA123456789BR", "--carrier", "100003", "--key", "k"]
        )
        lookups = mocked.requests[("POST", URL(f"{_BASE}/gettracklist"))]
        assert ("POST", URL(f"{_BASE}/changecarrier")) not in mocked.requests
    assert [call.kwargs["json"]["page_no"] for call in lookups] == [1, 2]
    assert result.exit_code == 2
    assert "2151" in result.stderr
    assert "190012" in result.stderr


def test_update_unregistered_number_exits_1(load_fixture: Any) -> None:
    payload = load_fixture("gettracklist_page")
    payload["page"].update(page_total=1, data_total=0)  # single page: no follow-up fetch
    payload["data"]["accepted"] = []
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/gettracklist", payload=payload)
        result = runner.invoke(
            app, ["update", "ZZ000000000XX", "--carrier", "190012", "--key", "k"]
        )
        assert ("POST", URL(f"{_BASE}/changecarrier")) not in mocked.requests
    assert result.exit_code == 1
    assert "not registered" in result.stderr


def test_update_without_fields_is_a_usage_error() -> None:
    with aioresponses() as mocked:
        result = runner.invoke(app, ["update", "AA123456789BR", "--key", "k"])
        assert not mocked.requests  # rejected before any network
    assert result.exit_code == 2
    assert "--carrier" in result.stderr
    assert "--tag" in result.stderr


def test_update_json_reports_one_result_per_field(load_fixture: Any) -> None:
    payload = load_fixture("gettracklist_page")
    payload["page"].update(page_total=1, data_total=1)  # single page: no follow-up fetch
    payload["data"]["accepted"] = [payload["data"]["accepted"][0]]  # AA... on carrier 2151
    with aioresponses() as mocked:
        mocked.post(f"{_BASE}/gettracklist", payload=payload)
        mocked.post(f"{_BASE}/changecarrier", callback=_echo_accepted)
        mocked.post(f"{_BASE}/changeinfo", callback=_echo_accepted)
        result = runner.invoke(
            app,
            [
                "update",
                "AA123456789BR",
                "--carrier",
                "190012",
                "--tag",
                "t",
                "--key",
                "k",
                "--json",
            ],
        )
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert set(parsed) == {"carrier", "tag"}
    assert [item["number"] for item in parsed["carrier"]["accepted"]] == ["AA123456789BR"]
    assert [item["number"] for item in parsed["tag"]["accepted"]] == ["AA123456789BR"]
    assert parsed["carrier"]["rejected"] == []
    assert parsed["tag"]["rejected"] == []


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


# --- webhook helpers stay library-only ---


def test_webhook_commands_are_not_exposed() -> None:
    """Webhook verification/parsing belongs in the receiving server, not a
    human CLI; the commands were removed on purpose and must not creep back."""
    help_result = runner.invoke(app, ["--help"])
    assert "webhook" not in help_result.stdout.casefold()
    assert runner.invoke(app, ["webhook-verify"]).exit_code == 2
    assert runner.invoke(app, ["webhook-parse"]).exit_code == 2
