"""#2370: UI names only safe Core-attested provider failures."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.connection_failure_summary import connection_failure_summary
from cifereca.gui.startup_coordinator import connect_configured_sources
from cifereca.gui.settings_diagnostics_refresh_controller import refresh_settings_diagnostics
from cifereca.gui import settings_diagnostics_refresh_controller as refresh_controller
from tests.test_core_ui_1127_settings_diagnostics_refresh_controller import make_context


def test_summary_groups_duplicate_sources_and_reports_closed_codes():
    projection = {
        "items": (
            {"provider_id": "provider.numilog", "code": "provider_connect_failed"},
            {"provider_id": "provider.numilog", "code": "provider_connect_failed"},
            {"provider_id": "source_inconnue", "code": "provider_not_registered"},
        ),
        "truncated": 0,
    }
    assert connection_failure_summary(projection) == (
        "Sources en échec : provider.numilog ×2 [provider_connect_failed], "
        "source_inconnue [provider_not_registered]"
    )


def test_summary_refuses_private_and_untrusted_details():
    projection = {"items": (
        {"provider_id": "provider.numilog", "code": "provider_connect_failed",
         "credential_id": "SECRET"},
        {"provider_id": "https://reader.invalid/?token=SECRET",
         "code": "provider_connect_failed"},
        {"provider_id": "user@private.invalid", "code": "provider_connect_failed"},
        {"provider_id": "provider.numilog", "code": "PRIVATE_SECRET"},
    ), "truncated": 0}
    summary = connection_failure_summary(projection)
    assert summary == "Sources en échec : provider.numilog [provider_connect_failed]"
    for forbidden in ("SECRET", "private", "https://", "credential_id"):
        assert forbidden not in summary
    assert connection_failure_summary({"items": "malformed"}) == ""


def test_long_projection_bounded_and_truncation_is_explicit():
    data = {"items": tuple({
        "provider_id": f"provider.test{i}", "code": "provider_connect_failed",
    } for i in range(10)), "truncated": 13}
    result = connection_failure_summary(data)
    assert result.count("[provider_connect_failed]") == 3
    assert "20 autre(s)" in result  # 7 omitted rows + 13 Core-truncated


def _connection_host(result):
    messages = []
    jobs = []
    created = []
    host = NS(
        _closed=False,
        _source_inventory_startup_connection_seen=True,
        service=NS(connect_configured=lambda: result, last_loans=[]),
        runner=NS(submit=lambda description, work, ok, err, **opts: ok(work())),
        _populate_accounts=lambda: jobs.append("accounts"),
        _populate_loans=lambda rows: jobs.append("loans"),
        _render_dashboard=lambda: jobs.append("dashboard"),
        _activity_result=lambda text, **kw: messages.append((text, kw)),
        run_opening_sync=lambda: jobs.append("sync"),
    )
    return host, messages, jobs, created


def test_startup_connection_banner_displays_source_and_no_private_key():
    payload = {
        "connected": ["one"],
        "errors": {"provider.numilog:PRIVATE_ACCOUNT": "PRIVATE_URL"},
        "connection_failures": {
            "items": ({"provider_id": "provider.numilog",
                       "code": "provider_connect_failed"},),
            "truncated": 0,
        },
    }
    host, messages, jobs, pending = _connection_host(payload)
    assert connect_configured_sources(host, idle_add=pending.append) is False
    assert jobs == ["accounts", "loans", "dashboard"]
    assert len(messages) == 1
    text, opts = messages[0]
    assert "1 source(s) connectée(s), 1 en erreur" in text
    assert "provider.numilog [provider_connect_failed]" in text
    assert "PRIVATE" not in text
    assert opts["success"] is False and opts["persistent"] is True
    # Reconnection does not re-trigger its work until existing lifecycle idle.
    assert len(pending) == 1


def test_connection_banner_retains_generic_message_without_safe_projection():
    host, messages, *_ = _connection_host(
        {"connected": [], "errors": {"private": "SECRET"}, "connection_failures": {
            "items": ({"provider_id": "account.PRIVATE", "code": "provider_connect_failed"},)
        }},
    )
    connect_configured_sources(host, idle_add=lambda callback: None)
    assert messages[0][0] == "0 source(s) connectée(s), 1 en erreur"


def test_diagnostics_summary_includes_live_connection_failures_without_network(monkeypatch):
    monkeypatch.setattr(refresh_controller, "DiagnosticsBrokerImpl",
                        lambda service: NS(snapshot=service._journal_snapshot))
    host, args, calls, report = make_context(ready=5, attention=0)
    snapshot = {
        "errors": {"connect_failed": 1},
        "connection_failures": {"items": (
            {"provider_id": "provider.kobo", "code": "provider_connect_failed"},
        ), "truncated": 0},
    }
    counter = []
    def read():
        counter.append(True)
        return snapshot
    host.service.provider_diagnostic_state = read
    refresh_settings_diagnostics(host, **args)
    label = args["diag_summary"].label
    assert "provider.kobo [provider_connect_failed]" in label
    assert "Aucune anomalie bloquante" not in label
    assert len(counter) == 1
    assert '"provider.kobo"' in label
    assert '"provider_id": "provider.kobo"' in report.text
    assert "Source: journal canonique local" in report.text
