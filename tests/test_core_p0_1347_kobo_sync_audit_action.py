"""#1345/#1347: the actual Synchronisation page audit is scoped and inert by default."""
from __future__ import annotations

from types import SimpleNamespace as NS

from tests.test_core_ui_1347_sync_action_scope import _fixture, _source, _source_row
from tests.test_core_p0_1345_source_audit_broker import COUNTS


def _receipt(*, truncated=False):
    return NS(
        ok=True,
        facts={
            "status": "audit_only", "phase": "reconcile",
            "offline": True, "read_only": True,
            "acquisition_authorized": False,
            "truncated": truncated,
            "counts": dict(COUNTS),
            "private": "/home/PRIVATE_SECRET_BOOK",
        },
    )


def _audit_controls(window):
    group = window._sync_sources_box.children[0]
    row = group.children[0]
    # An unsupported/disconnected source has no ghost Sync button (#2141).
    # Find the audit by its action, not an old positional suffix index.
    matches = [button for button in row.suffixes
               if button.label == "Vérifier les fichiers…"]
    assert len(matches) == 1
    return matches[0], group.children[2]


def test_sync_page_audit_is_explicit_and_updates_redacted_summary():
    window, broker, calls, _, _ = _fixture()
    audit_button, audit_row = _audit_controls(window)
    assert audit_button.sensitive
    assert "Aucun téléchargement" not in audit_row.subtitle  # fixed no-write warning
    assert "aucun téléchargement" in audit_row.subtitle
    assert not window.runner.tasks
    assert not any(name == "audit" for name, *_ in calls)
    audit_button.click()
    assert len(window.runner.tasks) == 1
    task = window.runner.tasks[0]
    assert task.options["kind"] == "sync-audit"
    assert task.options["control"] is audit_button
    assert "lecture seule" in audit_row.subtitle
    assert not any(name == "audit" for name, *_ in calls)
    task.work()
    assert ("audit", "kobo-one") in calls
    task.done(_receipt())
    assert "SHA : 3" in audit_row.subtitle
    assert "aucune réconciliation, acquisition ni suppression autorisée" in audit_row.subtitle.lower()
    assert "PRIVATE" not in audit_row.subtitle


def test_audit_remains_available_if_kobo_is_disabled_and_disconnected():
    source = _source(enabled=False, connected=False, sync_capable=False)
    window, broker, calls, _, _ = _fixture(sources=[source])
    row = _source_row(window)
    # Disabled public/unsupported sync is absent, not an index placeholder.
    assert not any(button.label and button.label.startswith("Synchroniser ")
                   for button in row.suffixes)
    previews = [button for button in row.suffixes
                if button.label == "Aperçu local…"]
    assert len(previews) == 1 and not previews[0].sensitive
    audit_button, _ = _audit_controls(window)
    assert audit_button.sensitive  # purely local audit remains available
    audit_button.click()
    assert window.runner.tasks[0].work() is not None
    assert ("audit", source.source_id) in calls
    assert not any(name == "sync-source" for name, *_ in calls)


def test_unsupported_sync_broker_disables_audit_without_provider_calls():
    window, broker, calls, _, _ = _fixture()
    broker.audit_local_reconciliation = None
    window._refresh_sync_page()
    button, output = _audit_controls(window)
    assert not button.sensitive
    button.click()
    assert window.runner.tasks == []
    assert not any(name == "audit" for name, *_ in calls)


def test_queued_audit_stops_on_identity_switch_without_file_io():
    window, broker, calls, _, _ = _fixture()
    button, output = _audit_controls(window)
    button.click()
    task = window.runner.tasks[0]
    window.service.current_identity = NS(identity_id="other")
    assert task.work() is None
    task.done(_receipt())
    assert "SHA" not in output.subtitle
    assert not any(name == "audit" for name, *_ in calls)


def test_old_button_and_completion_are_inert_after_page_refresh():
    window, broker, calls, _, _ = _fixture()
    old_button, old_output = _audit_controls(window)
    old_button.click()
    task = window.runner.tasks[0]
    window._refresh_sync_page()
    old_button.click()
    assert len(window.runner.tasks) == 1
    assert task.work() is None
    task.done(_receipt())
    assert "SHA" not in old_output.subtitle
    assert not any(name == "audit" for name, *_ in calls)


def test_disposed_window_rejects_audit_work_and_result():
    window, broker, calls, _, _ = _fixture()
    button, output = _audit_controls(window)
    button.click()
    task = window.runner.tasks[0]
    window._closed = True
    assert task.work() is None
    task.done(_receipt())
    assert "SHA" not in output.subtitle
    assert not any(name == "audit" for name, *_ in calls)


def test_revisited_same_identity_does_not_resurrect_old_audit():
    window, broker, calls, _, _ = _fixture()
    window._identity_context_generation = 10
    window._refresh_sync_page()
    button, output = _audit_controls(window)
    button.click()
    task = window.runner.tasks[0]
    window._identity_context_generation = 12
    # even after A -> B -> A, work retains its original generation
    assert task.work() is None
    task.done(_receipt())
    assert not any(name == "audit" for name, *_ in calls)
    assert "SHA" not in output.subtitle


def test_broker_exception_is_not_shown_to_user():
    window, broker, calls, _, _ = _fixture()
    button, output = _audit_controls(window)
    button.click()
    task = window.runner.tasks[0]
    task.error(RuntimeError("https://PRIVATE_SERVER/?token=PRIVATE"))
    assert output.subtitle == "Audit local indisponible. Aucun fichier modifié."
