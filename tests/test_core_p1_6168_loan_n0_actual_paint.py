"""#6168 N0/N3/N4: truthful GTK texture paint from exact Core decision."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core.book_cover_cache import CoverDecisionCache
from cifereca.core.book_cover_source_projection import (
    exact_loan_cover_scope, project_exact_loan_cover,
)
from cifereca.gui.cover_loader import CoverMediaState
from cifereca.gui.loan_cover_trace_bridge import LoanCoverTraceSubscriptions
from tests.test_core_p1_4979_exact_loan_cover import loan, local_row


CONTEXT = ("alice", 42, 7)


class Physical:
    def __init__(self, *, source=True, preview=False):
        self.probes = []
        self.source = source
        self.preview = preview

    def probe_media(self, uri):
        self.probes.append(uri)
        return CoverMediaState(
            "embedded", self.source, self.preview, None, None,
        )


def start(*, current=None, complete=True, selected=True,
          cache=None):
    current = current or loan()
    rows = (local_row(current),) if selected else ()
    decision = project_exact_loan_cover(
        current, rows, revision=3, snapshot_complete=complete,
    )
    cache = cache or CoverDecisionCache()
    observer = LoanCoverTraceSubscriptions()
    assert observer.watch(cache, decision.scope, context=CONTEXT)
    token = cache.begin(decision.scope, revision=3)
    assert cache.publish_resolved(token, decision)
    return cache, observer, decision, token


def paint(cache, watcher, decision, *, context=CONTEXT,
          uri=None, surface="list", loader=None, revision=3):
    return watcher.record_applied_texture(
        cache, decision.scope, context=context, revision=revision,
        uri=uri if uri is not None else decision.uri,
        surface=surface, loader=loader or Physical(),
    )


def test_real_n2_selected_n3_cached_and_gtk_texture_are_three_distinct_stages():
    cache, watcher, decision, token = start()
    before = watcher.snapshot(decision.scope, context=CONTEXT)
    assert before.status == "selected" and before.resolved
    assert not before.media_cached and not before.list_painted
    physical = Physical(source=True, preview=True)
    assert paint(cache, watcher, decision, loader=physical)
    listed = watcher.snapshot(decision.scope, context=CONTEXT)
    assert physical.probes == [decision.uri]
    assert listed.media_cached and listed.preview_cached
    assert listed.list_painted and not listed.detail_painted
    assert paint(cache, watcher, decision, surface="detail")
    after = watcher.snapshot(decision.scope, context=CONTEXT)
    assert after.list_painted and after.detail_painted
    assert after.generation == token.generation


def test_no_physical_cache_hit_is_never_declared_texture_painted():
    cache, watcher, decision, _ = start()
    physical = Physical(source=False)
    assert not paint(cache, watcher, decision, loader=physical)
    state = watcher.snapshot(decision.scope, context=CONTEXT)
    assert state.resolved
    assert not state.media_cached
    assert not state.list_painted and not state.detail_painted


def test_invalidation_or_late_same_revision_rejects_old_gtk_result():
    cache, watcher, decision, old_token = start()
    cache.invalidate(decision.scope, cause="account_epoch_changed")
    assert not paint(cache, watcher, decision)
    fresh = cache.begin(decision.scope, revision=3)
    assert fresh.generation > old_token.generation
    assert cache.publish_resolved(fresh, decision)
    assert paint(cache, watcher, decision)
    state = watcher.snapshot(decision.scope, context=CONTEXT)
    assert state.generation == fresh.generation


def test_same_image_other_loan_account_and_wrong_context_never_mark_paint():
    cache, watcher, decision, _ = start()
    other = loan(account="different-account")
    other_scope = exact_loan_cover_scope(other)
    assert other_scope != decision.scope
    assert not watcher.record_applied_texture(
        cache, other_scope, context=CONTEXT, revision=3,
        uri=decision.uri, surface="list", loader=Physical(),
    )
    for context in (
        ("alice", 43, 7), ("alice", 42, 8),
        ("bob", 42, 7),
    ):
        assert not paint(cache, watcher, decision, context=context)
    assert not watcher.snapshot(decision.scope, context=CONTEXT).list_painted


@pytest.mark.parametrize("input_uri", [
    "https://covers.example.org/foreign.jpg",
    "file:///home/foreign/cover.jpg",
    "",
])
def test_other_selected_uri_cannot_be_passed_off_as_this_loan(input_uri):
    cache, watcher, decision, _ = start()
    assert not paint(cache, watcher, decision, uri=input_uri)


def test_revision_and_expiration_must_match_same_live_n2():
    now = [0.0]
    cache = CoverDecisionCache(
        selected_ttl=1.0, clock=lambda: now[0],
    )
    cache, watcher, decision, _ = start(cache=cache)
    assert not paint(cache, watcher, decision, revision=4)
    now[0] = 1.5
    assert not paint(cache, watcher, decision)
    state = watcher.snapshot(decision.scope, context=CONTEXT)
    assert not state.list_painted


@pytest.mark.parametrize("complete", [True, False])
def test_absent_or_pending_core_evidence_never_marks_a_gtk_paint(complete):
    cache, watcher, decision, _ = start(
        selected=False, complete=complete,
    )
    assert decision.status in ("missing", "pending")
    assert not watcher.record_applied_texture(
        cache, decision.scope, context=CONTEXT,
        revision=3, uri="https://covers.example.org/unproved.jpg",
        surface="list", loader=Physical(),
    )
    assert not watcher.snapshot(decision.scope, context=CONTEXT).media_cached


def test_probe_failure_and_untrusted_media_report_do_not_mask_valid_gtk_image():
    cache, watcher, decision, _ = start()
    class Failed:
        def probe_media(self, _uri):
            raise OSError("cache temporarily inaccessible")
    assert not paint(cache, watcher, decision, loader=Failed())
    class Malformed:
        def probe_media(self, _uri):
            return {"source_cached": True}
    assert not paint(cache, watcher, decision, loader=Malformed())
    assert not paint(cache, watcher, decision, loader=object())
    assert not watcher.snapshot(decision.scope, context=CONTEXT).list_painted


def test_bad_surface_and_disposal_fail_closed():
    cache, watcher, decision, _ = start()
    with pytest.raises(ValueError):
        paint(cache, watcher, decision, surface="other")
    watcher.dispose()
    assert not paint(cache, watcher, decision)
    assert cache.stats()["subscribers"] == 0


def test_public_n0_fields_prove_gtk_paint_without_exposing_book_coordinates():
    cache, watcher, decision, _ = start()
    assert paint(cache, watcher, decision)
    fields = watcher.snapshot(decision.scope, context=CONTEXT).public_fields()
    assert fields["list_painted"] is True
    assert fields["detail_painted"] is False
    for secret in (
        decision.uri, decision.scope.identity_id,
        decision.scope.account_id, decision.scope.credential_id,
        decision.scope.source_key,
    ):
        assert secret not in repr(fields)
