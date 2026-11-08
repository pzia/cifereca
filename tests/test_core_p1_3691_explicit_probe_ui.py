"""#3691: UI admission must be visible, diagnostic and side-effect-free.

These fixtures are synthetic; never import captures, real credentials or URLs.
"""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui import settings_editor_actions
from tests.test_settings_page_composition import Widget


PROVIDER = "biblioaccess.la-rochelle"


def _harness(monkeypatch, *, enabled_sentinel=True):
    saved = {
        "id": "credential-1", "provider": PROVIDER,
        "identity_id": "identity-1", "label": "La Rochelle",
        "credentials": {"username": "PRIVATE-SYNTHETIC-USER"},
        "options": {"experimental_http_probe": "auto-post"},
    }
    if enabled_sentinel != "missing":
        saved["enabled"] = enabled_sentinel
    captured = []
    editor_rows = []

    def account_builder(**kwargs):
        data = kwargs["data"]
        e = {
            "group": Widget(), "deleted": False,
            "enabled": Widget(active=data.get("enabled", True) is True),
            "identity": Widget(text=data["identity_id"]),
            "label": Widget(text=data["label"]),
            "fields": {"username": Widget(text="PRIVATE-SYNTHETIC-USER")},
            "option_fields": {"experimental_http_probe": Widget(text="auto-post")},
            "explicit_probe_button": Widget(),
            "explicit_probe_status": Widget(),
        }
        editor_rows.append(e)
        return e, Widget()

    monkeypatch.setattr(settings_editor_actions, "build_provider_account_editor", account_builder)
    monkeypatch.setattr(settings_editor_actions, "build_identity_settings_header",
                        lambda **kw: Widget())
    monkeypatch.setattr(settings_editor_actions, "build_provider_accounts_header",
                        lambda **kw: NS(plugins_button=Widget(), accounts_container=Widget()))
    monkeypatch.setattr(settings_editor_actions, "build_provider_account_buttons",
                        lambda **kw: ())

    descriptor = NS(
        display_name="La Rochelle", state="ready",
        account_options=(NS(key="experimental_http_probe", default="off"),),
    )
    submits = []
    entries = []
    def add(category, operation, status="ok", *, detail):
        entries.append((category, operation, status, detail))
    session_action = Mock(return_value=NS(status="observed"))
    host = NS(
        config=NS(identities=lambda: [], provider_accounts=lambda: [dict(saved)]),
        service=NS(
            accounts=(), last_loans=(),
            diagnostics=NS(add=add),
            explicit_provider_diagnostic=session_action,
        ),
        runner=NS(submit=lambda *args, **kwargs: submits.append((args, kwargs))),
        navigate=Mock(),
        _closed=False,
    )
    settings_editor_actions.build_settings_editors(
        host, box=Widget(), descriptors={PROVIDER: descriptor}, public_ids=set(),
        mark_settings_dirty=Mock(), library_directory=lambda: "/books",
        Gtk=NS(Button=Widget), Adw=NS(), add_css=Mock(),
    )
    assert len(editor_rows) == 1
    return editor_rows[0], submits, entries, session_action


def test_legacy_enabled_default_matches_config_and_can_run_explicit_probe(monkeypatch):
    editor, submits, entries, action = _harness(
        monkeypatch, enabled_sentinel="missing")
    button = editor["explicit_probe_button"]
    assert button.sensitive is True
    assert submits == [] and action.call_count == 0
    button.emit("clicked")
    assert len(submits) == 1
    assert [row[3]["phase"] for row in entries] == ["clicked", "dispatched"]
    args, kwargs = submits[0]
    assert kwargs["control"] is button
    result = args[1]()
    action.assert_called_once_with(
        identity_id="identity-1", provider_id=PROVIDER,
        credential_id="credential-1", user_consent=True)
    args[2](result)
    assert editor["explicit_probe_button"].sensitive is True
    assert [row[3]["phase"] for row in entries] == [
        "clicked", "dispatched", "completed_unverified"]
    assert not any("PRIVATE" in repr(row) for row in entries)


@pytest.mark.parametrize("flag", [False, "true", 1, None, {}, []])
def test_disabled_or_corrupt_saved_account_explains_no_test(monkeypatch, flag):
    editor, submits, entries, action = _harness(monkeypatch, enabled_sentinel=flag)
    assert editor["explicit_probe_button"].sensitive is False
    assert "Enregistrer et activer" in editor["explicit_probe_status"].subtitle
    # Mock signal emission bypasses GTK's insensitive-button behavior.
    editor["explicit_probe_button"].emit("clicked")
    assert not submits and action.call_count == 0
    assert [x[3]["phase"] for x in entries] == [
        "clicked", "blocked_unavailable"]


def test_modified_option_reports_blocked_unsaved_without_connect(monkeypatch):
    editor, submits, entries, action = _harness(monkeypatch)
    editor["option_fields"]["experimental_http_probe"].text = "on"
    editor["explicit_probe_button"].emit("clicked")
    assert not submits and action.call_count == 0
    assert "Enregistrer les modifications" in editor["explicit_probe_status"].subtitle
    assert [row[3]["phase"] for row in entries] == ["clicked", "blocked_unsaved"]
    assert not any("PRIVATE" in repr(row) for row in entries)


def test_failure_ui_does_not_copy_exception_or_credentials_to_journal(monkeypatch):
    editor, submits, entries, action = _harness(monkeypatch)
    button = editor["explicit_probe_button"]
    button.emit("clicked")
    args, _kwargs = submits[0]
    args[3](RuntimeError("PRIVATE-SYNTHETIC-SERVER-RESPONSE"))
    assert editor["explicit_probe_button"].sensitive is True
    assert [row[3]["phase"] for row in entries][-1] == "failed"
    assert "PRIVATE" not in repr(entries)
    assert "PRIVATE" not in editor["explicit_probe_status"].subtitle
    action.assert_not_called()
