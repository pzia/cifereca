"""Synthetic end-to-end Core operator review of historical evidence."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cifereca.core import adept_historical_maintenance as review
from cifereca.gui.adept_historical_maintenance_line import _display
from tests.test_core_p0_3171_atomic_publication_v14 import (
    store, register, new_source, target, publish, rows, BYTES,
)


def _ledger(store, *, aid="history-0", identity="A", account="buyer-A",
            credential="card-A", loan="loan-1"):
    record = {
        "acquisition_id": aid, "identity_id": identity, "provider_id": "seller",
        "credential_id": credential, "account_id": account, "item_id": "edition",
        "status": "complete", "strategy": "loan_auto", "executor": "kobo-adept",
        "metadata": {"provider_loan_id": loan, "adept_binding": {
            "adept_profile_id": "historical-adobe", "adept_loan_id": "adobe-loan",
            "loan_state": "returned", "inventory_status": "resolved",
            "inventory_checked_at": 1234,
        }},
    }
    store.acquisition_put(record)
    return record


def _setup(store, tmp_path, *, account="buyer-A"):
    source, snapshot = new_source(kind="loan", branch="loans", right="loan-1", account=account)
    register(store, "A", "seller", "card-A", account=account)
    root = tmp_path / "EBooks"
    path = target(tmp_path, "A")
    publication = publish(store, root, path, source, snapshot, adept_receipt_ref="opaque-receipt")
    _ledger(store, account=account)
    service = SimpleNamespace(storage=store, config={"library.directory": str(root)},
                              current_identity=SimpleNamespace(identity_id="A", directory_name="A"),
                              _account_context_epoch=0)
    return service, source, path, publication


def _run(service, **kwargs):
    request = review.admit_historical_review(service)
    assert request is not None
    return review.run_historical_review(service, request, **kwargs)


def test_historical_observations_are_independent_and_read_only(store, tmp_path):
    service, _, path, _ = _setup(store, tmp_path)
    baseline = rows(store), store.acquisition_get("history-0")
    page = _run(service)
    row, = page["rows"]
    assert row["historical_license_state"] == "returned_recorded"
    assert row["recorded_adept_profile_id"] == "historical-adobe"
    assert row["inventory_checked_at"] == 1234
    assert row["publication"]["canonical_origin_records"] == "provider_and_adept_recorded"
    assert row["local_bytes"]["selection"] == "not_requested"
    assert row["provider_loan_state"] == "not_checked"
    assert not row["activation_attested"] and not row["inventory_transport_attested"]
    assert not page["mutation_authorized"] and not page["complete_provider_inventory"]
    physical = _run(service, verify_local_bytes=True)["rows"][0]
    assert physical["local_bytes"]["evidence"]["placement_evidence"] == "verified_artifact_bytes"
    assert "historical-adobe" in _display(page)
    assert "opaque-receipt" not in _display(page)
    assert baseline == (rows(store), store.acquisition_get("history-0"))
    assert path.read_bytes() == BYTES


def test_same_loan_other_account_not_adopted_and_legacy_gap_visible(store, tmp_path):
    service, _, _, _ = _setup(store, tmp_path)
    _ledger(store, aid="history-1", account="other")
    _ledger(store, aid="history-2", loan="other")
    reports = _run(service)["rows"]
    assert reports[0]["canonical_status"] == "unique_source_record"
    for row in reports[1:]:
        assert row["canonical_status"] == "source_not_recorded"
        assert row["publication"] is None and row["local_bytes"] is None
        assert row["historical_license_state"] == "returned_recorded"


def test_multiple_branches_block_candidate(store, tmp_path, monkeypatch):
    service, _, _, _ = _setup(store, tmp_path)
    with store._lock, store._connect() as db:
        db.execute(
            "INSERT INTO source_references (source_reference_id,identity_id,provider_id,"
            "credential_id,account_id,branch_id,edition_id,right_id,reference_kind,created_at,updated_at) "
            "VALUES ('other-ref','A','seller','card-A','buyer-A','other-branch','edition','loan-1','loan',1,1)"
        )
    monkeypatch.setattr(review, "review_historical_loan_diagnostic",
                        Mock(side_effect=AssertionError("ambiguous branch")))
    row, = _run(service, verify_local_bytes=True)["rows"]
    assert row["canonical_status"] == "ambiguous_branch"
    review.review_historical_loan_diagnostic.assert_not_called()


def test_page_exact_identity_and_revocation(store, tmp_path):
    service, _, _, _ = _setup(store, tmp_path)
    register(store, "B", "seller", "card-B", account="buyer-B")
    _ledger(store, aid="history-B", identity="B", credential="card-B", account="buyer-B")
    for i in range(1, 6):
        _ledger(store, aid=f"history-{i}", loan=f"loan-{i+1}")
    page = _run(service)
    assert [row["acquisition_id"] for row in page["rows"]] == [
        "history-0", "history-1", "history-2", "history-3",
    ]
    rest = _run(service, cursor=page["next_cursor"])
    assert [row["acquisition_id"] for row in rest["rows"]] == ["history-4", "history-5"]
    assert rest["next_cursor"] is None
    request = review.admit_historical_review(service)
    service._account_context_epoch += 1
    assert review.run_historical_review(service, request) is None


def test_page_changed_during_review_is_discarded(store, tmp_path, monkeypatch):
    service, _, _, _ = _setup(store, tmp_path)
    original = review._row
    def changed(request, row, verify):
        value = original(request, row, verify)
        with store._lock, store._connect() as db:
            db.execute("UPDATE acquisition_ledger SET status='retryable_error' WHERE acquisition_id='history-0'")
        return value
    monkeypatch.setattr(review, "_row", changed)
    assert _run(service) is None


def test_empty_primary_account_is_not_unknown_scope(store, tmp_path):
    service, _, _, _ = _setup(store, tmp_path, account="")
    row, = _run(service, verify_local_bytes=True)["rows"]
    assert row["account_id"] == ""
    assert row["canonical_status"] == "unique_source_record"
    assert row["local_bytes"]["evidence"]["placement_evidence"] == "verified_artifact_bytes"


def test_native_maintenance_hook_exists_with_or_without_tools():
    import ast
    main = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
    tree = ast.parse(main.read_text())
    method = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == "_refresh_tools_page")
    calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)
             and node.func.id == "add_adept_historical_maintenance_line"]
    assert len(calls) == 2
    assert any(any(k.arg == 'mode' and isinstance(k.value, ast.Constant)
                   and k.value.value == 'holds' for k in call.keywords) for call in calls)


def test_hold_review_includes_marker_without_ledger_and_never_unlocks(store, tmp_path):
    from dataclasses import replace
    from cifereca.core.canonical_adept_attempt import _key, _value
    from cifereca.core.canonical_sync_stage import persist_attested_source_references
    from cifereca.gui.adept_historical_maintenance_line import _display_holds
    service, source, path, _ = _setup(store, tmp_path)
    # A distinct loan was observed but never reached a completed receipt.
    pending, snapshot = new_source(kind="loan", branch="loans", right="pending-loan")
    persist_attested_source_references(store, snapshot)
    marker = _value("synthetic-offer", "f" * 32)
    store.metadata_set(_key(pending), marker)
    service.execute_acquisition_request = Mock(side_effect=AssertionError("no acquisition"))
    request = review.admit_historical_review(service)
    page = review.run_historical_hold_review(service, request)
    row, = page["rows"]
    assert row["provider_loan_id"] == pending.right_id
    assert row["hold_origin"] == "marker_recorded"
    assert not row["unlock_authorized"] and not page["mutation_authorized"]
    assert row["replay_proof"] == "not_established"
    assert "pending-loan" in _display_holds(page) and "non autorisé" in _display_holds(page)
    assert marker not in repr(page) and "f" * 32 not in _display_holds(page)
    assert store.metadata_get(_key(pending)) == marker
    assert path.read_bytes() == BYTES
    service.execute_acquisition_request.assert_not_called()
    service._account_context_epoch += 1
    assert review.run_historical_hold_review(service, request) is None


def test_hold_review_pages_and_exact_owner(store, tmp_path):
    from cifereca.core.canonical_adept_attempt import _key, _value
    from cifereca.core.canonical_sync_stage import persist_attested_source_references
    service, source, path, _ = _setup(store, tmp_path)
    for index in range(5):
        key, snapshot = new_source(kind="loan", branch="loans", right=f"held-{index}")
        persist_attested_source_references(store, snapshot)
        store.metadata_set(_key(key), _value("offer", "a"*32))
    register(store, "B", "seller", "card-B", account="buyer-B")
    key, snapshot = new_source("B", credential="card-B", account="buyer-B",
                               kind="loan", branch="loans", right="foreign-loan")
    persist_attested_source_references(store, snapshot)
    store.metadata_set(_key(key), _value("offer", "b"*32))
    request = review.admit_historical_review(service)
    first = review.run_historical_hold_review(service, request)
    second = review.run_historical_hold_review(service, request, offset=first["next_cursor"])
    assert first["known_holds"] == second["known_holds"] == 5
    assert first["next_cursor"] == 4 and second["next_cursor"] is None
    assert {row["provider_loan_id"] for row in first["rows"] + second["rows"]} == {
        f"held-{index}" for index in range(5)
    }
    assert all(not row["unlock_authorized"] for row in first["rows"] + second["rows"])


def test_hold_review_nested_transaction_preserves_caller(store, tmp_path):
    service, _, _, _ = _setup(store, tmp_path)
    request = review.admit_historical_review(service)
    db = store._connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO metadata(key,value) VALUES('synthetic.hold.caller','pending')")
        assert review.run_historical_hold_review(service, request) is None
        assert db.in_transaction
        assert db.execute("SELECT value FROM metadata WHERE key='synthetic.hold.caller'").fetchone() == ("pending",)
    finally:
        if db.in_transaction:
            db.rollback()


@pytest.mark.parametrize("kind", ["canonical", "padded", "blob", "oversized"])
def test_hold_review_classifies_exact_marker_without_exporting_witness(store, tmp_path, kind):
    import sqlite3
    from cifereca.core.canonical_adept_attempt import _key, _value
    from cifereca.core.canonical_sync_stage import persist_attested_source_references
    from cifereca.gui.adept_historical_maintenance_line import _display_holds
    service, _, path, _ = _setup(store, tmp_path)
    pending, snapshot = new_source(kind="loan", branch="loans", right="blocked")
    persist_attested_source_references(store, snapshot)
    raw = _value("synthetic-offer", "f" * 32)
    if kind == "padded":
        raw += " "
    elif kind == "oversized":
        raw += " " * 8192
    elif kind == "blob":
        raw = sqlite3.Binary(raw.encode("ascii"))
    with store._lock, store._connect() as sql:
        sql.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (_key(pending), raw))
    request = review.admit_historical_review(service)
    page = review.run_historical_hold_review(service, request)
    row, = page["rows"]
    assert row["hold_origin"] == "marker_recorded"
    assert row["marker_state"] == ("held_phase_unknown" if kind == "canonical" else "held_invalid")
    expected = "phase non attestée" if kind == "canonical" else "conserver le verrou"
    assert expected in _display_holds(page)
    assert "fingerprint" not in repr(page) and "f" * 32 not in repr(page)
    assert not row["unlock_authorized"] and not page["mutation_authorized"]
    with store._lock, store._connect() as sql:
        observed, = sql.execute("SELECT value FROM metadata WHERE key=?", (_key(pending),)).fetchone()
    assert observed == (bytes(raw) if kind == "blob" else raw)
    assert path.read_bytes() == BYTES


@pytest.mark.parametrize("with_publication", [False, True])
def test_historical_hold_explicit_physical_review_never_unlocks(store, tmp_path, with_publication):
    from cifereca.core.canonical_adept_attempt import _key, _value
    from cifereca.core.canonical_sync_stage import persist_attested_source_references
    from cifereca.gui.adept_historical_maintenance_line import _display_holds

    service, published, path, _ = _setup(store, tmp_path)
    if with_publication:
        held = published
    else:
        held, observed = new_source(kind="loan", branch="loans", right="pending-only")
        persist_attested_source_references(store, observed)
    marker = _value("synthetic-same-offer", "a" * 32)
    store.metadata_set(_key(held), marker)
    request = review.admit_historical_review(service)
    prior = review.run_historical_hold_review(service, request)
    assert prior["rows"][0]["local_copy_evidence"] == "not_requested"
    page = review.run_historical_hold_review(
        service, request, verify_local_bytes=True,
    )
    row, = page["rows"]
    assert row["provider_loan_id"] == held.right_id
    assert row["local_copy_evidence"] == (
        "verified_local_copy_observed" if with_publication else "not_proven"
    )
    assert not row["unlock_authorized"] and not page["mutation_authorized"]
    assert row["activation_attested"] is False
    assert "non autorisé" in _display_holds(page)
    assert marker not in repr(page)
    assert store.metadata_get(_key(held)) == marker
    assert path.read_bytes() == BYTES


def test_hold_review_rejects_non_boolean_file_review_without_read(store, tmp_path, monkeypatch):
    service, _source, _path, _ = _setup(store, tmp_path)
    request = review.admit_historical_review(service)
    monkeypatch.setattr(
        review, "review_historical_loan_diagnostic",
        Mock(side_effect=AssertionError("bad flag must not read files")),
    )
    assert review.run_historical_hold_review(
        service, request, verify_local_bytes=1,
    ) is None


def test_historical_hold_preview_aborts_if_marker_changes_during_physical_review(
    store, tmp_path, monkeypatch,
):
    from cifereca.core.canonical_adept_attempt import _key, _value

    service, held, path, _ = _setup(store, tmp_path)
    before = _value("original-offer", "a" * 32)
    after = _value("changed-offer", "b" * 32)
    store.metadata_set(_key(held), before)
    request = review.admit_historical_review(service)
    assert request is not None
    original = review.review_historical_loan_diagnostic

    def marker_changed(*args, **kwargs):
        result = original(*args, **kwargs)
        # A concurrent Sync changes the exact v1 marker while the operator
        # hashes a local candidate. A stale page must not be presented.
        store.metadata_set(_key(held), after)
        return result

    monkeypatch.setattr(review, "review_historical_loan_diagnostic", marker_changed)
    assert review.run_historical_hold_review(
        service, request, verify_local_bytes=True,
    ) is None
    assert store.metadata_get(_key(held)) == after
    assert path.read_bytes() == BYTES



def test_historical_ledger_review_rejects_changed_branch_snapshot(
    store, tmp_path, monkeypatch,
):
    """Other SQL evidence may change while the displayed ledger stays intact."""
    service, _source, path, _publication = _setup(store, tmp_path)
    request = review.admit_historical_review(service)
    assert request is not None
    assert review.run_historical_review(service, request)["rows"][0][
        "canonical_status"
    ] == "unique_source_record"
    original = review._row
    modified = []

    def competing_branch(req, row, verify):
        result = original(req, row, verify)
        if not modified:
            modified.append(True)
            with store._lock, store._connect() as sql:
                sql.execute(
                    "INSERT INTO source_references (source_reference_id,identity_id,"
                    "provider_id,credential_id,account_id,branch_id,edition_id,"
                    "right_id,reference_kind,created_at,updated_at) VALUES "
                    "('concurrent-branch','A','seller','card-A','buyer-A',"
                    "'another-branch','edition','loan-1','loan',1,1)"
                )
        return result

    monkeypatch.setattr(review, "_row", competing_branch)
    # The repeated ledger page itself is unchanged: the independent
    # source-reference evidence would be stale without a WAL stamp.
    assert review.run_historical_review(service, request) is None
    assert modified == [True]
    assert path.read_bytes() == BYTES
    monkeypatch.setattr(review, "_row", original)
    row, = review.run_historical_review(service, request)["rows"]
    assert row["canonical_status"] == "ambiguous_branch"


def test_grouped_historical_and_hold_views_share_core_without_duplicate_actions(store, tmp_path):
    from types import SimpleNamespace as NS
    from cifereca.core.canonical_adept_attempt import _key, _value
    from cifereca.gui.adept_historical_maintenance_line import add_adept_historical_maintenance_line
    from tests.test_core_ui_1358_maintenance_preview_cancellation import (
        Button as BaseButton, Container as BaseContainer,
    )

    class Button(BaseButton):
        def set_label(self, label): self.label = label

    class Container(BaseContainer):
        def __init__(self):
            super().__init__()
            self.visible = True
        def set_visible(self, visible): self.visible = visible

    class Label(Button):
        def __init__(self, **kwargs):
            super().__init__()
            self.text = ""
        def set_wrap(self, _): pass
        def set_selectable(self, _): pass
        def set_text(self, text): self.text = text

    class Combo(Button):
        def __init__(self):
            super().__init__()
            self.active = -1
            self.items = []
        def remove_all(self): self.items.clear()
        def append_text(self, text): self.items.append(text)
        def set_active(self, active): self.active = active
        def get_active(self): return self.active

    service, held, path, _ = _setup(store, tmp_path)
    marker = _value("offer", "a" * 32)
    store.metadata_set(_key(held), marker)
    baseline = rows(store)
    jobs = []
    host = NS(service=service, _closed=False, _route_display_token=object(),
              _identity_context_generation=1,
              action_button=lambda label, _icon: Button(label),
              runner=NS(submit=lambda *args, **kwargs: jobs.append((args, kwargs))))
    gtk = NS(Box=lambda **kw: Container(), Button=Button, Label=Label,
             ComboBoxText=Combo, Orientation=NS(HORIZONTAL=1, VERTICAL=2))
    adw = NS(PreferencesGroup=Container, ActionRow=Container)
    provenance, recovery = Container(), Container()
    assert add_adept_historical_maintenance_line(host, provenance, Gtk=gtk, Adw=adw, mode="provenance")
    assert add_adept_historical_maintenance_line(host, recovery, Gtk=gtk, Adw=adw, mode="holds")
    prow, hrow = provenance.children[0].children[0], recovery.children[0].children[0]
    assert prow.title == "Provenances de prêts historiques"
    assert hrow.title == "Verrous de téléchargements ADEPT"
    examine, = prow.children
    blocked, = hrow.children
    assert examine.label == "Examiner…" and blocked.label == "Verrous…"
    assert len(provenance.children) == 4 and len(recovery.children) == 4
    assert not provenance.children[2].visible and not recovery.children[2].visible
    assert not jobs
    blocked.click()
    args, kwargs = jobs[-1]
    assert kwargs["control"] is blocked
    args[2](args[1]())
    assert "Retéléchargement non autorisé" in recovery.children[-1].text
    assert recovery.children[2].visible
    assert provenance.children[-1].text == ""
    # Keep the actual grouped controller selection witness from #7480.
    selector, inspect = recovery.children[2].children
    assert len(selector.items) == 1 and not inspect.sensitive
    selector.set_active(0)
    selector.handlers["changed"](selector)
    assert inspect.sensitive
    before = len(jobs)
    inspect.click()
    assert len(jobs) == before + 1
    args, kwargs = jobs[-1]
    assert kwargs["control"] is inspect
    args[2](args[1]())
    assert "Verrou sélectionné :" in recovery.children[-1].text
    assert "Retéléchargement non autorisé" in recovery.children[-1].text
    assert held.right_id in recovery.children[-1].text
    assert marker not in recovery.children[-1].text
    assert store.metadata_get(_key(held)) == marker
    assert rows(store) == baseline
    examine.click()
    args, kwargs = jobs[-1]
    assert kwargs["control"] is examine
    args[2](args[1]())
    assert "history-0" in provenance.children[-1].text
    assert "Retéléchargement non autorisé" in recovery.children[-1].text
    # A new explicit hash request stays in the recovery surface.
    verify = recovery.children[1].children[0]
    verify.click()
    args, _ = jobs[-1]
    host._identity_context_generation += 1
    args[2](args[1]())
    assert not recovery.children[-1].visible
    assert rows(store) == baseline and store.metadata_get(_key(held)) == marker
    assert path.read_bytes() == BYTES

@pytest.mark.parametrize("case", ["exact", "changed", "orphan"])
def test_historical_hold_reports_correlated_non_submission_without_permit(store, tmp_path, case):
    from cifereca.core import canonical_adept_attempt as marker
    from cifereca.core.canonical_sync_stage import persist_attested_source_references
    from cifereca.gui.adept_historical_maintenance_line import _display_holds
    from cifereca.core.loan_download_retry_review import review_loan_download_retries
    service, _, path, _ = _setup(store, tmp_path)
    pending, snapshot = new_source(kind="loan", branch="loans", right="blocked-witness")
    persist_attested_source_references(store, snapshot)
    raw = marker._value("synthetic-offer", "f" * 32)
    proof = marker._unsubmitted_value(pending, raw, "core_pre_dispatch_refusal")
    store.metadata_set(marker._unsubmitted_key(pending), proof)
    if case != "orphan":
        store.metadata_set(marker._key(pending),
            raw if case == "exact" else marker._value("other-offer", "a" * 32))
    retry, = review_loan_download_retries(service, identity_id="A")
    assert retry.adept_hold and not retry.can_reset
    request = review.admit_historical_review(service)
    page = review.run_historical_hold_review(service, request)
    row, = page["rows"]
    assert row["non_submission_evidence"] == (
        "non_submission_recorded" if case == "exact" else "unverified")
    assert row["hold_origin"] == (
        "unsubmitted_witness_orphan" if case == "orphan" else "marker_recorded")
    assert row["replay_proof"] == "not_established" and not row["unlock_authorized"]
    rendered = _display_holds(page)
    assert "non autorisé" in rendered
    assert ("Refus Core avant appel runtime enregistré" in rendered) == (case == "exact")
    assert proof not in repr(page) and raw not in repr(page) and "fingerprint" not in repr(page)
    assert path.read_bytes() == BYTES
    assert store.metadata_get(marker._unsubmitted_key(pending)) == proof


@pytest.mark.parametrize("unsafe", [
    "line\nForged activation", "escape\x1b[2J", "", " " * 4, "x" * 1025, None,
])
def test_historical_hold_redacts_unsafe_borrowed_loan_title(
    store, tmp_path, monkeypatch, unsafe,
):
    from dataclasses import replace
    from cifereca.core import loan_download_retry_review as queue
    from cifereca.core.canonical_adept_attempt import _key, _value
    from cifereca.core.canonical_sync_stage import persist_attested_source_references
    from cifereca.gui.adept_historical_maintenance_line import _display_holds

    service, _first, path, _ = _setup(store, tmp_path)
    selected_source, snapshot = new_source(kind="loan", branch="loans",
                                           right="untrusted-title")
    persist_attested_source_references(store, snapshot)
    marker = _value("offer", "a" * 32)
    store.metadata_set(_key(selected_source), marker)

    original = queue.review_loan_download_retries
    def unsafe_queue(*args, **kwargs):
        return tuple(replace(row, title=unsafe) for row in original(*args, **kwargs))
    monkeypatch.setattr(queue, "review_loan_download_retries", unsafe_queue)
    request = review.admit_historical_review(service)
    page = review.run_historical_hold_review(service, request)
    assert page is not None
    row, = page["rows"]
    assert row["title"] == "Référence de prêt historique"
    assert "Forged activation" not in _display_holds(page)
    assert not row["unlock_authorized"] and not page["mutation_authorized"]
    index = 0
    selected = review.select_historical_hold(
        service, request, page, ordinal=index, confirmed=True,
    )
    assert selected is not None and not selected.unlock_authorized
    assert store.metadata_get(_key(selected_source)) == marker
    assert path.read_bytes() == BYTES
