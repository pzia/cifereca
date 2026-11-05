"""P1 #1063: ADEPT runtime inspection happens only on the saved-row click."""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui import settings_editor_actions
from cifereca.gui.paris_adept_presentation import normal_paris_adept_runtime_summary
from cifereca.gui.settings_lifetime import SettingsPageLifetime
from tests.test_settings_page_composition import Widget


SAVED_STATUS = {
    "identity_id": "alice", "status": "ready",
    "reason": "local_configuration_complete",
    "paris_label": "Paris", "adept_profile_id": "adept-a",
}


def _harness(monkeypatch):
    import copy
    saved = {
        "id": "alice", "label": "Alice", "primary_paris_connection_id": "paris-a",
        "adept_profile": "adept-a",
    }
    rows = []
    def builder(**kwargs):
        eid = Widget(kwargs["identity_id"])
        eid.get_text = lambda: eid.text
        profile = Widget(kwargs["adept_profile"])
        primary = Widget(kwargs["data"].get("primary_paris_connection_id", ""))
        check = Widget()
        status = Widget()
        editor = {
            "id": eid, "directory": Widget(), "adept_profile": profile,
            "primary_paris_connection_id": primary, "normal_adept_check": check,
            "normal_adept_status": status, "group": Widget(), "deleted": False,
        }
        choose, adept, remove = Widget(), Widget(), Widget()
        rows.append((editor, check))
        return editor, choose, adept, remove

    monkeypatch.setattr(settings_editor_actions, "build_identity_settings_editor", builder)
    monkeypatch.setattr(
        settings_editor_actions, "build_identity_settings_header",
        lambda **kw: Widget(),
    )
    monkeypatch.setattr(
        settings_editor_actions, "build_provider_accounts_header",
        lambda **kw: NS(plugins_button=Widget(), accounts_container=Widget()),
    )
    monkeypatch.setattr(
        settings_editor_actions, "build_provider_account_buttons",
        lambda **kw: (),
    )
    submits = []
    runner = NS(submit=lambda *args, **kw: submits.append((args, kw)))
    identities = [saved]
    profile_reads = []
    def runtime(identity):
        profile_reads.append(identity)
        return {
            "identity_id": identity, "status": "observed",
            "reason": "adept_acsm_capability_reported", "acsm_ready": True,
        }
    service = NS(
        storage=NS(adept_profile_binding_get=lambda *a: "adept-a"),
        normal_paris_adept_status=lambda identity: copy.deepcopy(SAVED_STATUS),
        check_normal_paris_adept_runtime=runtime,
        accounts=[],
    )
    host = NS(
        config=NS(identities=lambda: identities, provider_accounts=lambda: (),
                  identity=lambda id: dict(saved)),
        service=service, runner=runner, navigate=Mock(),
        _closed=False, _identity_context_generation=1,
        _account_context_generation=1,
        _choose_identity_directory=Mock(), _choose_adept_profile=Mock(),
    )
    lifetime = SettingsPageLifetime()
    settings_editor_actions.build_settings_editors(
        host, box=Widget(), descriptors={}, public_ids=set(),
        mark_settings_dirty=Mock(), library_directory=lambda: "/books",
        Gtk=NS(Button=Widget), Adw=NS(), add_css=Mock(),
        page_lifetime=lifetime,
    )
    assert len(rows) == 1
    return host, rows[0][0], rows[0][1], submits, profile_reads, lifetime


def test_no_profile_enumeration_on_render_only_after_explicit_click(monkeypatch):
    host, editor, button, submits, reads, lifetime = _harness(monkeypatch)
    assert not submits and not reads
    button.emit("clicked")
    assert len(submits) == 1 and not reads
    # UI pending gate blocks an immediate double click.
    button.emit("clicked")
    assert len(submits) == 1
    args, opts = submits[0]
    assert opts["control"] is button
    result = args[1]()
    assert reads == ["alice"]
    args[2](result)
    assert button.sensitive is True
    assert "Autorisation Adobe et emprunt non testés" in (
        editor["normal_adept_status"].subtitle
    )
    assert lifetime.alive


def test_draft_edit_rejects_verification_before_runtime_call(monkeypatch):
    host, editor, button, submits, reads, _ = _harness(monkeypatch)
    editor["primary_paris_connection_id"].text = "paris-b"
    button.emit("clicked")
    assert not submits and not reads
    assert "enregistrez" in editor["normal_adept_status"].subtitle


def test_late_runtime_result_is_ignored_after_aba_or_page_disposal(monkeypatch):
    host, editor, button, submits, reads, lifetime = _harness(monkeypatch)
    button.emit("clicked")
    args, _ = submits[0]
    response = args[1]()
    host._identity_context_generation = 2
    args[2](response)
    assert not hasattr(editor["normal_adept_status"], "subtitle")
    # The check was done against prior state and can never update a disposed page.
    host._identity_context_generation = 1
    button.emit("clicked")
    assert len(submits) == 2
    lifetime.dispose()
    submits[1][0][2](response)
    assert not hasattr(editor["normal_adept_status"], "subtitle")
    assert len(reads) == 1


def test_runtime_presentation_never_translates_unknown_into_adobe_ready():
    assert "À vérifier" in normal_paris_adept_runtime_summary({
        "reason": "adept_profile_not_found",
    })
    assert "non publiée" in normal_paris_adept_runtime_summary({
        "reason": "adept_acsm_readiness_unknown",
    })
    assert "Autorisation Adobe" in normal_paris_adept_runtime_summary({
        "status": "observed", "reason": "adept_acsm_capability_reported",
        "acsm_ready": True,
    })
