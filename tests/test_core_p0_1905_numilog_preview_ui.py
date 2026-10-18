"""Numilog local evidence is explicit, scoped, offline and never download consent."""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_view import sync_numilog_local_preview_presentation as present
from tests.test_core_ui_1347_sync_action_scope import _fixture, _source


def receipt(verify=False):
    counts = dict.fromkeys((
        "owned_purchases", "already_local_linked", "already_local_verified",
        "needs_file_verification", "potentially_missing", "candidate_unverified",
        "needs_reconciliation", "attention_required", "not_downloadable",
        "ignored_unattested_rights",
    ), 0)
    counts.update(owned_purchases=3, potentially_missing=2, candidate_unverified=2)
    return NS(ok=True, facts=dict(
        phase="local_materialization", status="preview_only", read_only=True,
        offline=True, acquisition_authorized=False, confirmation_available=False,
        schedule_available=False, manifest_refreshed=False,
        requires_manifest_validation=True, files_verified=verify,
        known_item_count=3, counts=counts,
        private="https://private.invalid/?token=PRIVATE",
    ))


def setup(*, enabled=True):
    sources = [_source(provider_id="numilog", source_id=f"numilog-{i}",
                       connected=False, enabled=enabled) for i in (1, 2)]
    w, broker, calls, build, _page = _fixture(sources=sources)
    def preview(sid, *, verify_local_files=False):
        calls.append(("numilog-preview", sid, verify_local_files))
        return receipt(verify_local_files)
    broker.preview_local_materialization = preview
    row = w._sync_sources_box.children[1].children[1]
    return w, broker, calls, build, row


@pytest.mark.parametrize("verify", [False, True])
def test_disconnected_preview_runs_only_explicit_exact_account_and_never_acquires(verify):
    w, broker, calls, _build, row = setup()
    assert not w.runner.tasks
    assert not any(c[0] == "numilog-preview" for c in calls)
    row.suffixes[int(verify)].click()
    task = w.runner.tasks[-1]
    assert "numilog-preview" not in repr(calls)
    result = task.work()
    task.done(result)
    assert calls[-1] == ("numilog-preview", "numilog-2", verify)
    assert "Achats connus : 3" in row.subtitle
    assert "aucun téléchargement autorisé" in row.subtitle
    assert "PRIVATE" not in row.subtitle
    assert not any(c[0] in {"acquire", "sync-source", "audit"} for c in calls)


@pytest.mark.parametrize("change", ["identity", "refresh", "rebuild", "close"])
def test_stale_preview_never_runs_or_publishes_into_replaced_page(change):
    w, _broker, calls, build, row = setup()
    row.suffixes[0].click()
    task = w.runner.tasks[-1]
    if change == "identity": w.service.current_identity = NS(identity_id="bob")
    if change == "refresh": w._refresh_sync_page()
    if change == "rebuild": build()
    if change == "close": w._closed = True
    before = row.subtitle
    assert task.work() is None
    task.done(receipt())
    task.error(RuntimeError("PRIVATE"))
    row.suffixes[1].click()
    assert row.subtitle == before and len(w.runner.tasks) == 1
    assert not any(c[0] == "numilog-preview" for c in calls)


def test_disabled_source_is_inert_and_old_preview_cannot_overwrite_newer_verification():
    w, _, _, _, row = setup(enabled=False)
    for button in row.suffixes: button.click()
    assert not w.runner.tasks
    w, _, _, _, row = setup()
    row.suffixes[0].click()
    old = w.runner.tasks[-1]
    row.suffixes[1].click()
    w.runner.tasks[-1].done(receipt(True))
    current = row.subtitle
    old.done(receipt(False))
    old.error(RuntimeError("PRIVATE"))
    assert row.subtitle == current and "Vérification SHA demandée" in current


@pytest.mark.parametrize("key,value", [
    ("offline", False), ("read_only", False), ("acquisition_authorized", True),
    ("confirmation_available", True), ("schedule_available", True),
    ("manifest_refreshed", True), ("known_item_count", True),
    ("files_verified", "PRIVATE"), ("status", "PRIVATE"),
])
def test_invalid_evidence_is_not_presented_as_safe_preview(key, value):
    result = receipt()
    result.facts[key] = value
    assert present(result) == "Aperçu local indisponible. Aucun téléchargement autorisé."


@pytest.mark.parametrize("count", [True, -1, 1000001, "PRIVATE", None])
def test_counter_types_bounds_and_private_strings_fail_closed(count):
    result = receipt()
    result.facts["counts"]["already_local_verified"] = count
    assert present(result) == "Aperçu local indisponible. Aucun téléchargement autorisé."
