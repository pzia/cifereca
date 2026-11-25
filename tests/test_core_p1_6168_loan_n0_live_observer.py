"""P1 #6168: live N0 evidence subscribed to the same Loan N2 decisions.

No GTK/HTTP or forged paint markers: observe the actual, already cached
Core loan decision from both presentation routes. Owner teardown cancels the
subscriptions; epochs and source identities cannot replay old observations.
"""
from __future__ import annotations

import ast
from pathlib import Path

from cifereca.core.book_cover_cache import CoverDecisionCache
from cifereca.core.book_cover_source_projection import exact_loan_cover_scope
from cifereca.gui.loan_cover_trace_bridge import LoanCoverTraceSubscriptions
from tests.test_core_p1_4979_late_library_cover_repaint import (
    host, snapshot_method,
)
from tests.test_core_p1_4979_loan_gtk_projection import (
    a_loan, a_row, ui_method,
)


ROOT = Path(__file__).resolve().parents[1]
LOAN_SOURCE = ROOT / "src/cifereca/gui/loan_cover_decision_bridge.py"
CLOSE_SOURCE = ROOT / "src/cifereca/gui/window_close.py"


def test_real_loan_core_publication_emits_n0_selected_without_false_paint():
    obj, loan, views = host()
    assert not hasattr(obj, "_loan_cover_trace_observer")
    snapshot_method()(obj, [a_row(loan)])
    assert views[-1] == "file:///home/alice/.cache/cifereca/cover.png"
    scope = exact_loan_cover_scope(loan)
    watcher = obj._loan_cover_trace_observer
    trace = watcher.snapshot(scope, context=("alice", 42, None))
    assert trace.status == "selected"
    assert trace.evidence_present and trace.resolved
    assert not trace.media_cached and not trace.preview_cached
    assert not trace.list_painted and not trace.detail_painted
    assert not trace.invalidated
    assert watcher.stats() == {"subscriptions": 1, "tracked_sources": 1}
    assert obj._loan_cover_decision_cache.stats()["subscribers"] == 1


def test_list_and_detail_observe_same_n0_revision_without_duplicate_listeners():
    obj, loan, _ = host()
    snapshot_method()(obj, [a_row(loan)])
    watcher = obj._loan_cover_trace_observer
    scope = exact_loan_cover_scope(loan)
    old = watcher.snapshot(scope, context=("alice", 42, None))
    assert ui_method()(obj, loan).thumbnail_url
    assert ui_method()(obj, loan).thumbnail_url
    assert watcher.stats()["subscriptions"] == 1
    assert watcher.snapshot(scope, context=("alice", 42, None)) is old
    assert old.revision == obj._loan_cover_decision_revision


def test_n0_missing_does_not_claim_evidence_or_thumbnail_for_foreign_borrower():
    obj, loan, _ = host()
    snapshot_method()(obj, [a_row(a_loan(account="foreign"))])
    state = obj._loan_cover_trace_observer.snapshot(
        exact_loan_cover_scope(loan), context=("alice", 42, None),
    )
    assert state.status == "missing"
    assert state.resolved
    assert not state.evidence_present
    assert not state.list_painted and not state.detail_painted


def test_a_to_b_to_a_and_source_epoch_changes_cannot_replay_n0_trace():
    obj, loan, _ = host()
    obj.service._source_context_epoch = 3
    snapshot_method()(obj, [a_row(loan)])
    watcher = obj._loan_cover_trace_observer
    scope = exact_loan_cover_scope(loan)
    assert watcher.snapshot(scope, context=("alice", 42, 3)).resolved

    obj.service.current_identity.identity_id = "bob"
    obj.service._account_context_epoch += 1
    assert watcher.snapshot(scope, context=("bob", 43, 3)) is None
    obj.service.current_identity.identity_id = "alice"
    assert watcher.snapshot(scope, context=("alice", 43, 3)) is None

    # Account re-evidence may be accepted in a new epoch but never reuses a
    # previous trace; the old N2 image is not a fresh GTK paint.
    snapshot_method()(obj, [a_row(loan)])
    fresh = watcher.snapshot(scope, context=("alice", 43, 3))
    assert fresh is not None and fresh.status == "selected"
    assert fresh.revision > 1
    assert not fresh.media_cached and not fresh.list_painted
    assert watcher.snapshot(scope, context=("alice", 42, 3)) is None

    obj.service._source_context_epoch = 4
    assert watcher.snapshot(scope, context=("alice", 43, 4)) is None
    # The same cached N2 revision is NOT promoted into an N0 observation
    # merely by a new source epoch; a real producer event is required.
    assert ui_method()(obj, loan).thumbnail_url
    assert watcher.snapshot(scope, context=("alice", 43, 4)) is None
    snapshot_method()(obj, [a_row(loan)])
    assert watcher.snapshot(scope, context=("alice", 43, 4)).resolved


def test_window_observer_is_bounded_and_cancels_evicted_subscriptions():
    cache = CoverDecisionCache()
    observer = LoanCoverTraceSubscriptions(max_scopes=2)
    scopes = [
        exact_loan_cover_scope(a_loan(account="account-" + str(i)))
        for i in range(3)
    ]
    for scope in scopes:
        assert observer.watch(cache, scope, context=("alice", 42, 7))
    assert observer.stats()["subscriptions"] == 2
    assert cache.stats()["subscribers"] == 2
    assert observer.snapshot(scopes[0], context=("alice", 42, 7)) is None
    observer.dispose()
    assert cache.stats()["subscribers"] == 0
    assert observer.stats()["subscriptions"] == 0
    assert observer.snapshot(scopes[-1], context=("alice", 42, 7)) is None
    observer.dispose()  # idempotent
    assert not observer.watch(cache, scopes[-1], context=("alice", 42, 7))


def test_replaced_cache_and_context_cancel_old_subscriber():
    first, second = CoverDecisionCache(), CoverDecisionCache()
    observer = LoanCoverTraceSubscriptions()
    scope = exact_loan_cover_scope(a_loan())
    assert observer.watch(first, scope, context=("alice", 1, 1))
    assert first.stats()["subscribers"] == 1
    assert observer.watch(second, scope, context=("alice", 2, 1))
    assert first.stats()["subscribers"] == 0
    assert second.stats()["subscribers"] == 1
    assert observer.snapshot(scope, context=("alice", 1, 1)) is None
    observer.dispose()
    assert second.stats()["subscribers"] == 0


def test_foreign_profile_and_malformed_context_are_rejected_without_subscription():
    cache = CoverDecisionCache()
    observer = LoanCoverTraceSubscriptions()
    scope = exact_loan_cover_scope(a_loan())
    for context in (
        ("bob", 1, 1), ("alice", -1, 1), ("alice", 1, True),
        ("alice", 1), ("alice", "1", 1), ["alice", 1, 1],
    ):
        assert not observer.watch(cache, scope, context=context)
    assert cache.stats()["subscribers"] == 0
    assert observer.stats()["tracked_sources"] == 0


def test_loan_bridge_and_window_close_use_real_owner_scoped_lifecycle():
    bridge = LOAN_SOURCE.read_text(encoding="utf-8")
    close = CLOSE_SOURCE.read_text(encoding="utf-8")
    ast.parse(bridge)
    ast.parse(close)
    assert "LoanCoverTraceSubscriptions(max_scopes=128)" in bridge
    assert "observer.watch(" in bridge
    assert bridge.index("observer.watch(") < bridge.index("hit = cache.peek(")
    assert "loan_cover_trace.dispose()" in close
    assert close.index("loan_cover_trace.dispose()") < close.index(
        "window.runner.close()"
    )


def test_missing_cover_publishes_closed_reason_once_for_current_n2_revision():
    from cifereca.diagnostics import Diagnostics

    obj, loan, views = host()
    obj.diagnostics = Diagnostics(level="detailed")
    row = a_row(loan)
    row.update(provider_id=loan.source_provider, item_id=loan.record_id)
    row.pop("_exact_loan_cover_proofs")
    snapshot_method()(obj, [row])
    assert views[-1] is None
    assert ui_method()(obj, loan).thumbnail_url is None
    assert ui_method()(obj, loan).thumbnail_url is None
    entries = obj.diagnostics.snapshot()
    assert len(entries) == 1
    assert entries[0]["category"] == "COVER"
    assert entries[0]["operation"] == "loan cover gap"
    assert entries[0]["detail"] == {
        "status": "missing",
        "reason": "visible_library_image_without_loan_proof",
        "library_candidate_observed": True,
        "exact_loan_proof_selected": False,
    }
    assert loan.thumbnail_url is None
    for secret in ("alice", "card-a", "account-a", "edition-1", "loan-1",
                   "file://", "cover.png"):
        assert secret not in repr(entries[0]["detail"])

    # A fresh proof, not the diagnostic hint, enables the loan cover.
    snapshot_method()(obj, [a_row(loan)])
    assert views[-1] == row["thumbnail_url"]
    assert len(obj.diagnostics.snapshot()) == 1
    obj.service._account_context_epoch += 1
    assert ui_method()(obj, loan).thumbnail_url is None
    assert len(obj.diagnostics.snapshot()) == 1


def test_missing_cover_diagnostics_are_opt_in_and_no_false_empty_snapshot():
    from cifereca.diagnostics import Diagnostics

    obj, loan, views = host()
    obj.diagnostics = Diagnostics(level="normal")
    row = a_row(loan)
    row.pop("_exact_loan_cover_proofs")
    snapshot_method()(obj, [row])
    assert views[-1] is None and obj.diagnostics.snapshot() == []
    obj.diagnostics.level = "detailed"
    snapshot_method()(obj, [])
    assert views[-1] is None and obj.diagnostics.snapshot() == []
    snapshot_method()(obj, [row])
    entry, = obj.diagnostics.snapshot()
    assert entry["detail"]["reason"] == "no_admissible_evidence"
