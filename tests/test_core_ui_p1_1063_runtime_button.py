"""P1 #1063: ADEPT runtime inspection happens only on the saved-row click."""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui import settings_editor_actions
from cifereca.gui.settings_lifetime import SettingsPageLifetime
from tests.test_settings_page_composition import Widget


SAVED_STATUS = {
    "identity_id": "alice", "status": "ready",
    "reason": "local_configuration_complete",
    "paris_label": "Paris", "adept_profile_id": "adept-a",
}


def _harness(monkeypatch, *, source=None, primary="paris-a"):
    import copy
    saved = {
        "id": "alice", "label": "Alice", "primary_paris_connection_id": primary,
        "adept_profile": "adept-a",
    }
    if source is not None:
        saved.update(
            adept_source_provider_id=source[0],
            adept_source_connection_id=source[1],
        )
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
            "primary_paris_connection_id": primary,
            "adept_source_provider_id": Widget(kwargs["data"].get("adept_source_provider_id", "")),
            "adept_source_connection_id": Widget(kwargs["data"].get("adept_source_connection_id", "")),
            "normal_adept_check": check,
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
    source_reads = []
    expected_source = source if source is not None else ("paris", "paris-a")
    def source_status(identity, *, source_provider_id, primary_connection_id, source_label=None):
        source_reads.append(("status", source_provider_id, primary_connection_id))
        if expected_source != (source_provider_id, primary_connection_id):
            return {"identity_id": identity, "status": "to_configure",
                    "reason": "invalid_source_descriptor"}
        return copy.deepcopy(SAVED_STATUS)

    def source_runtime(identity, *, source_provider_id, primary_connection_id,
                       source_label=None):
        source_reads.append(("runtime", source_provider_id, primary_connection_id))
        if expected_source != (source_provider_id, primary_connection_id):
            raise AssertionError("ADEPT probe retargeted to a different source")
        return runtime(identity)

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
        normal_source_adept_status=source_status,
        check_source_adept_runtime=source_runtime,
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
    host._adept_source_reads = source_reads
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
    assert "À vérifier" in settings_editor_actions._adept_runtime_caption({
        "reason": "adept_profile_not_found",
    })
    assert "non publiée" in settings_editor_actions._adept_runtime_caption({
        "reason": "adept_acsm_readiness_unknown",
    })
    assert "Autorisation Adobe" in settings_editor_actions._adept_runtime_caption({
        "status": "observed", "reason": "adept_acsm_capability_reported",
        "acsm_ready": True,
    })


def test_queued_adept_probe_uses_original_core_after_window_replacement(monkeypatch):
    host, editor, button, submits, reads, _ = _harness(monkeypatch)
    original_core = host.service
    button.emit("clicked")
    args, _ = submits[-1]

    def wrong_probe(_identity_id):
        raise AssertionError("queued ADEPT inspection ran against rebound Core")

    # Same visible identity and unchanged UI generations still must not
    # authorize publishing an old Core's private runtime observation.
    host.service = NS(
        storage=original_core.storage,
        normal_paris_adept_status=original_core.normal_paris_adept_status,
        check_normal_paris_adept_runtime=wrong_probe,
    )
    result = args[1]()
    assert result["identity_id"] == "alice"
    assert reads == ["alice"]
    args[2](result)
    assert not hasattr(editor["normal_adept_status"], "subtitle")
    assert button.sensitive is True


def test_queued_adept_probe_keeps_method_bound_at_click(monkeypatch):
    host, editor, button, submits, reads, _ = _harness(monkeypatch)
    button.emit("clicked")
    args, _ = submits[-1]
    host.service.check_source_adept_runtime = (
        lambda _identity_id, **_kwargs: (_ for _ in ()).throw(
            AssertionError("live method was rebound after admission")
        )
    )
    assert args[1]()["reason"] == "adept_acsm_capability_reported"
    assert reads == ["alice"]
    args[2]({"status": "observed", "reason": "adept_acsm_capability_reported",
             "acsm_ready": True})
    assert "Autorisation Adobe" in editor["normal_adept_status"].subtitle


def test_selected_non_paris_source_uses_exact_saved_scope_only(monkeypatch):
    host, editor, button, submits, reads, _ = _harness(
        monkeypatch, source=("future-library", "reader-b"),
    )
    assert host._adept_source_reads == [
        ("status", "future-library", "reader-b"),
    ]
    button.emit("clicked")
    assert len(submits) == 1 and not reads
    args, _opts = submits[0]
    result = args[1]()
    assert result["status"] == "observed"
    assert host._adept_source_reads[-1] == (
        "runtime", "future-library", "reader-b",
    )
    args[2](result)
    assert "Autorisation Adobe et emprunt non testés" in (
        editor["normal_adept_status"].subtitle
    )
    assert reads == ["alice"]


def test_changed_source_pair_cannot_probe_saved_adept_profile(monkeypatch):
    host, editor, button, submits, reads, _ = _harness(
        monkeypatch, source=("future-library", "reader-b"),
    )
    editor["adept_source_connection_id"].text = "reader-other"
    button.emit("clicked")
    assert not submits and not reads
    assert all(row[0] != "runtime" for row in host._adept_source_reads)
    assert "enregistrez" in editor["normal_adept_status"].subtitle


def test_saved_partial_source_never_falls_back_to_paris(monkeypatch):
    from cifereca.gui.settings_editor_actions import (
        _saved_adept_status, _selected_adept_source,
    )
    class Core:
        def normal_paris_adept_status(self, _identity):
            raise AssertionError("malformed source may not select Paris")
        def normal_source_adept_status(self, *_args, **_kwargs):
            raise AssertionError("incomplete source cannot be inspected")

    malformed = {
        "adept_source_provider_id": "future-library",
        "adept_source_connection_id": "",
    }
    assert _selected_adept_source(malformed) == (True, None, None)
    assert _saved_adept_status(Core(), "alice", malformed)["status"] == "to_configure"
    assert _selected_adept_source({"adept_source_provider_id": True}) == (
        True, None, None,
    )


def test_generic_source_queued_result_is_discarded_if_saved_scope_changes(monkeypatch):
    host, editor, button, submits, reads, _ = _harness(
        monkeypatch, source=("future-library", "reader-b"),
    )
    button.emit("clicked")
    args, _opts = submits[-1]
    result = args[1]()
    previous_config = host.config
    host.config = NS(
        identities=previous_config.identities,
        provider_accounts=previous_config.provider_accounts,
        identity=lambda _id: dict(
            previous_config.identity(_id),
            adept_source_connection_id="reader-other",
        ),
    )
    args[2](result)
    assert reads == ["alice"]
    assert not hasattr(editor["normal_adept_status"], "subtitle")


def test_historical_paris_uses_generic_source_core_facade(monkeypatch):
    host, editor, button, submits, reads, _ = _harness(monkeypatch)
    assert host._adept_source_reads == [("status", "paris", "paris-a")]
    # A runtime operation is always bound to the saved Paris connection,
    # even though the old Core method remains available to other callers.
    host.service.normal_paris_adept_status = lambda _id: (
        _ for _ in ()
    ).throw(AssertionError("retired Paris Settings status facade"))
    host.service.check_normal_paris_adept_runtime = lambda _id: (
        _ for _ in ()
    ).throw(AssertionError("retired Paris Settings inspection facade"))
    button.emit("clicked")
    assert len(submits) == 1
    result = submits[0][0][1]()
    assert result["status"] == "observed"
    submits[0][0][2](result)
    assert host._adept_source_reads[-1] == ("status", "paris", "paris-a")
    assert any(row == ("runtime", "paris", "paris-a")
               for row in host._adept_source_reads)
    assert reads == ["alice"]


def test_invalid_historical_primary_stays_non_authorizing(monkeypatch):
    from cifereca.gui.settings_editor_actions import (
        _saved_adept_status, _selected_adept_source,
    )
    class Core:
        def normal_source_adept_status(self, *_args, **_kwargs):
            raise AssertionError("malformed legacy primary cannot select a source")

    invalid = {"primary_paris_connection_id": True}
    assert _selected_adept_source(invalid) == (False, None, None)
    assert _saved_adept_status(Core(), "alice", invalid)["status"] == "to_configure"


def test_neutral_runtime_result_rejected_when_sqlite_adept_profile_changes(monkeypatch):
    """An external SQLite mutation must veto even unchanged UI epochs/status."""
    for source in (None, ("future-library", "reader-b")):
        host, editor, button, submits, reads, _ = _harness(
            monkeypatch, source=source,
        )
        old_core = host.service
        button.emit("clicked")
        assert len(submits) == 1
        args, _ = submits[0]
        observed = args[1]()
        assert observed["status"] == "observed"
        assert reads == ["alice"]
        # The mock status remains READY and the same source/GTK fields stay
        # visible; only the durable ADEPT profile binding has changed.
        old_core.storage.adept_profile_binding_get = lambda *_args: "adept-b"
        args[2](observed)
        assert not hasattr(editor["normal_adept_status"], "subtitle")
        assert button.sensitive is True


def test_neutral_runtime_result_rejected_on_saved_source_and_profile_read_errors(monkeypatch):
    """Provider-neutral callback is fail-closed even when persistence is down."""
    for source in (None, ("future-library", "reader-b")):
        for failed_reader in ("config", "sqlite"):
            host, editor, button, submits, _reads, _ = _harness(
                monkeypatch, source=source,
            )
            button.emit("clicked")
            assert len(submits) == 1
            args, _ = submits[0]
            observed = args[1]()
            if failed_reader == "config":
                host.config.identity = lambda _id: (_ for _ in ()).throw(
                    OSError("synthetic external config outage")
                )
            else:
                host.service.storage.adept_profile_binding_get = (
                    lambda *_args: (_ for _ in ()).throw(
                        OSError("synthetic SQLite outage")
                    )
                )
            args[2](observed)
            assert not hasattr(editor["normal_adept_status"], "subtitle")


def test_neutral_runtime_result_rejected_if_saved_primary_mutates_but_status_stays_ready(
    monkeypatch,
):
    for source in (None, ("future-library", "reader-b")):
        host, editor, button, submits, reads, _ = _harness(
            monkeypatch, source=source,
        )
        button.emit("clicked")
        args, _ = submits[0]
        observed = args[1]()
        old_identity = host.config.identity
        host.config.identity = lambda _id: dict(
            old_identity(_id), primary_paris_connection_id="paris-b",
        )
        args[2](observed)
        assert reads == ["alice"]
        assert not hasattr(editor["normal_adept_status"], "subtitle")


def test_neutral_runtime_result_rejected_if_external_saved_provider_pair_changes(
    monkeypatch,
):
    host, editor, button, submits, _reads, _ = _harness(
        monkeypatch, source=("future-library", "reader-b"),
    )
    button.emit("clicked")
    args, _ = submits[0]
    observed = args[1]()
    old_identity = host.config.identity
    host.config.identity = lambda _id: dict(
        old_identity(_id), adept_source_provider_id="other-library",
    )
    args[2](observed)
    assert not hasattr(editor["normal_adept_status"], "subtitle")


def test_revoked_explicit_adept_source_does_not_fall_back_on_runtime_click(monkeypatch):
    # Empty persisted keys are a deliberate negative marker after clearing a
    # previously selected source. Even a saved Paris primary must not become
    # the fallback runtime target.
    host, editor, button, submits, reads, _ = _harness(
        monkeypatch, source=("", ""),
    )
    assert host._adept_source_reads == []
    assert not reads
    button.emit("clicked")
    assert submits == [] and reads == []
    assert "À configurer" in editor["normal_adept_status"].subtitle


def test_late_adept_result_rejected_when_saved_primary_changes_without_ui_epoch(monkeypatch):
    host, editor, button, submits, reads, _ = _harness(monkeypatch)
    button.emit("clicked")
    args, _ = submits[-1]
    result = args[1]()
    # External config mutation: neither the rendered form nor UI generations
    # change, and the legacy descriptive local status still says "ready".
    host.config.identity = lambda _id: {
        "id": "alice", "primary_paris_connection_id": "paris-b",
    }
    args[2](result)
    assert reads == ["alice"]
    assert not hasattr(editor["normal_adept_status"], "subtitle")


def test_late_adept_result_rejected_when_saved_profile_changes_without_ui_epoch(monkeypatch):
    host, editor, button, submits, reads, _ = _harness(monkeypatch)
    button.emit("clicked")
    args, _ = submits[-1]
    result = args[1]()
    # A new SQLite ADEPT binding cannot inherit a positive result observed
    # for the previous profile, even when a read-only status is unchanged.
    host.service.storage.adept_profile_binding_get = lambda *_: "adept-b"
    args[2](result)
    assert reads == ["alice"]
    assert not hasattr(editor["normal_adept_status"], "subtitle")


def test_late_adept_result_rejected_when_persisted_binding_read_fails(monkeypatch):
    host, editor, button, submits, reads, _ = _harness(monkeypatch)
    button.emit("clicked")
    args, _ = submits[-1]
    result = args[1]()

    def unavailable(*_args):
        raise RuntimeError("synthetic private SQLite failure")

    host.service.storage.adept_profile_binding_get = unavailable
    args[2](result)
    assert reads == ["alice"]
    assert button.sensitive is True
    assert not hasattr(editor["normal_adept_status"], "subtitle")


def test_falsey_persisted_primary_mutation_cannot_be_coerced_into_stale_success(
    monkeypatch,
):
    """An exact Config/SQLite fence cannot treat 0/False/None like empty ID."""
    for mutation in (None, False, 0):
        host, editor, button, submits, reads, _ = _harness(
            monkeypatch, source=("future-library", "reader-b"), primary="",
        )
        button.emit("clicked")
        assert len(submits) == 1
        args, _ = submits[0]
        result = args[1]()
        assert reads == ["alice"]
        original = host.config.identity
        host.config.identity = lambda _id, value=mutation: dict(
            original(_id), primary_paris_connection_id=value,
        )
        # Both the generic ADEPT readiness status and the visible source
        # fields stay unchanged; only the exact saved legacy coordinate has
        # become malformed, without a GTK generation increment.
        args[2](result)
        assert not hasattr(editor["normal_adept_status"], "subtitle")
