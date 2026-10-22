"""#2698: actual public context/export/feed integration without GTK/Chromium.

The headed crash and GTK recipes run separately in their dedicated runtime.
This test exercises the new diagnostic bridge on the normal Core runner too.
"""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import PluginContext, PluginDirectories
from cifereca.plugins.manager import PluginManager
from cifereca.diagnostics import Diagnostics
from cifereca.core.diagnostic_event_feed import query_diagnostic_events

SOURCE = Path(__file__).resolve().parents[1] / "src/plugins/browser-recorder"


class Entry:
    def __init__(self, value):
        self.value = value
    def get_text(self):
        return self.value
    def set_text(self, value):
        self.value = value


@pytest.mark.parametrize("bad_key", [False, True])
@pytest.mark.parametrize("current", [False, True])
def test_private_export_crosses_public_context_to_attested_feed(
    tmp_path, monkeypatch, bad_key, current,
):
    monkeypatch.syspath_prepend(str(SOURCE))
    from browser_recorder_http import PrivateExchange, PrivateHttpTrace
    from browser_recorder_vault import PrivateSessionVault
    from browser_recorder_page import BrowserRecorderPage
    from browser_recorder_review_ui import PrivateReviewPanel

    secret = "FICTIONAL-PRIVATE-ACCOUNT-NEVER-IN-DIAGNOSTICS"
    password = "FICTIONAL-PRIVATE-VAULT-KEY-2026"
    trace = PrivateHttpTrace(("https://fixture.example.org",))
    trace.exchanges.append(PrivateExchange(
        1, "GET", "https://fixture.example.org/catalog?AccountId=" + secret,
        status=200, mime="application/json",
        response_body='{"AccountId":"' + secret + '"}',
        response_body_state="captured",
    ))
    vault = PrivateSessionVault(tmp_path / "vault")
    writer = vault.begin(password)
    writer.save(trace, "recording", force=True)
    trace.destroy()
    source = vault.path / (writer.session_id + ".cbrv")
    before = source.read_bytes()
    chosen = tmp_path / "chosen"
    chosen.mkdir(mode=0o755)

    collector = Diagnostics()
    plugin_id = "ui.page.browser-recorder"
    package_id = "org.cifereca.technical.browser-recorder"
    manager = NS(diagnostics=collector, _records={plugin_id: NS(
        descriptor=NS(id=plugin_id), package=NS(manifest=NS(package=NS(id=package_id))),
    )})
    context = PluginContext(
        plugin_id, package_id, PluginDirectories(tmp_path / "data", tmp_path / "cache"),
        _diagnostic_reporter=lambda message, status, detail, level: PluginManager._plugin_diag(
            manager, plugin_id, message, status, detail, level,
        ),
    )
    page = BrowserRecorderPage()
    page.context = context
    panel = object.__new__(PrivateReviewPanel)
    panel._closed, panel._handle = False, None
    panel._generation = 0
    panel._private_destination_dialog = None
    panel.is_scanning = lambda: False
    panel.vault_directory = vault.path
    # Deliberately select an unrelated catalogue ID when exporting the
    # active review. Never let an old list selection redirect its contents.
    other_sid = "0" * 32
    assert other_sid != writer.session_id
    panel.session_list = NS(get_active_id=lambda: other_sid if current else writer.session_id)
    panel._trace = NS(vault_session_id=writer.session_id) if current else None
    panel._prompt_private_zip_file = lambda accepted, cancelled: accepted(chosen / "session.zip")
    panel.vault_passphrase = Entry(password if not bad_key else "FICTIONAL-WRONG-VAULT-KEY")
    panel._diagnostic = page._report_http_outcome
    panel._refresh = lambda: None
    panel.status = NS(set_label=lambda _: None)
    panel.export_private_zip(current=current)
    # Removing the view/context cannot erase an already published result.
    page.context = None
    panel._closed = True
    assert panel.vault_passphrase.get_text() == ""
    assert source.read_bytes() == before
    result = query_diagnostic_events(collector, source="core_events", log_path=tmp_path / "absent")
    assert result["total"] == 1
    detail = result["events"][0]["detail"]
    assert detail["code"] == ("private_zip_export_failed" if bad_key else "private_zip_exported")
    assert type(detail["duration_ms"]) is int and detail["duration_ms"] >= 0
    files = list(chosen.iterdir())
    if bad_key:
        assert not files and detail["output_bytes"] == 0
    else:
        assert len(files) == 1 and files[0].suffix == ".zip"
        assert files[0].stat().st_mode & 0o777 == 0o600
        assert detail["output_bytes"] == files[0].stat().st_size
    errors = query_diagnostic_events(collector, source="core_events", severity="error", log_path=tmp_path / "absent")
    assert errors["total"] == int(bad_key)
    for rendered in (repr(result), repr(collector.snapshot()), collector.text("test")):
        assert secret not in rendered and password not in rendered
        assert str(chosen) not in rendered


def test_current_private_export_is_cancelled_if_active_review_changes_during_save_as(
    tmp_path, monkeypatch,
):
    """Changing reviewed session while chooser is open never redirects export."""
    monkeypatch.syspath_prepend(str(SOURCE))
    from browser_recorder_review_ui import PrivateReviewPanel

    panel = object.__new__(PrivateReviewPanel)
    panel._closed = False
    panel._handle = None
    panel._generation = 12
    panel._private_destination_dialog = None
    panel.vault_directory = tmp_path / "vault"
    panel._trace = NS(vault_session_id="a" * 32)
    panel.session_list = NS(get_active_id=lambda: "b" * 32)
    panel.is_recording = lambda: False
    panel.is_scanning = lambda: False
    panel.vault_passphrase = Entry("FICTIONAL-VALID-VAULT-KEY")
    messages, events, callbacks = [], [], []
    panel.status = NS(set_label=messages.append)
    panel._diagnostic = lambda op, code, **metrics: events.append((op, code, metrics))
    panel._prompt_private_zip_file = lambda accepted, cancelled: callbacks.append(
        (accepted, cancelled)
    )

    panel.export_private_zip(current=True)
    assert panel.vault_passphrase.get_text() == ""
    assert len(callbacks) == 1
    panel._trace = NS(vault_session_id="c" * 32)
    callbacks[0][0](tmp_path / "must-not-exist.zip")
    assert not (tmp_path / "must-not-exist.zip").exists()
    assert len(events) == 1
    assert events[0][:2] == ("private_zip_export", "private_zip_export_cancelled")
    assert events[0][2]["output_bytes"] == 0
    assert "FICTIONAL-VALID-VAULT-KEY" not in repr((messages, events))

