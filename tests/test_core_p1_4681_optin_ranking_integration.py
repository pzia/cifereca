"""P1 #4681: persisted Core feedback is consumed only with explicit opt-in.

Real Core SQLite + Metadata broker + Recommendations ranking + shared public
catalogue sessions. Provider replies are synthetic; no network or rights GET.
"""
from dataclasses import replace
from pathlib import Path
import sys

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "src" / "plugins" / "recommendations")
)

import pytest

import recommendation_service as recommendation_service
from recommendation_engine import ExplicitReadingSignal
from cifereca.brokers import MetadataBrokerImpl
from cifereca.core.explicit_reading_preferences import change_preference

from tests.test_core_p1_4909_recommendations_integration import _Metadata
from tests.test_core_p0_4673_paris_foreign_search import _fixture as paris_setup
from tests.test_core_p1_4909_numilog_shared_public import setup as numilog_setup
from tests.test_core_p1_4909_kobo_shared_public import setup as kobo_setup


def _setup(tmp_path, monkeypatch, provider):
    if provider == "paris":
        service, _, session, _, _ = paris_setup(tmp_path, monkeypatch)
    elif provider == "numilog":
        service, session, _ = numilog_setup(tmp_path, monkeypatch)
    else:
        service, session, _ = kobo_setup(tmp_path, monkeypatch)
    service._sessions = {"bob-only": session}
    books = [
        {**row, "key": f"book:{i}", "identity_id": "alice"}
        for i, row in enumerate(_Metadata().library_entries(identity_id="alice", scan=False))
    ]

    def entries(*, identity_id, scan):
        assert identity_id == "alice" and scan is False
        return list(books)

    monkeypatch.setattr(service, "library_entries", entries)
    original = session.search
    session.search = lambda query, scope, **kwargs: [
        replace(row, author="Alice") for row in original(query, scope, **kwargs)
    ]
    return service, session, MetadataBrokerImpl(service)


def _rank(meta, service, *, optin):
    partials = []
    result = recommendation_service.compute_recommendations(
        meta, service, "alice", max_queries=1, exploration=0,
        exclude_owned=False, use_saved_explicit_preferences=optin,
        partial=partials.append,
    )
    return result, partials


@pytest.mark.parametrize("provider", ["paris", "numilog", "kobo"])
def test_saved_feedback_changes_only_explicit_ranking_path(
    tmp_path, monkeypatch, provider,
):
    service, session, metadata = _setup(tmp_path, monkeypatch, provider)
    calls = []
    original = recommendation_service.build_profile

    def watch(books, *, identity_id=None, explicit_signals=None):
        calls.append(explicit_signals)
        return original(
            books, identity_id=identity_id, explicit_signals=explicit_signals,
        )

    monkeypatch.setattr(recommendation_service, "build_profile", watch)
    try:
        baseline, baseline_parts = _rank(metadata, service, optin=False)
        assert baseline.ranked and baseline_parts and calls[-1] is None
        snapshot = service.reading_preference_snapshot(identity_id="alice")
        assert snapshot.revision == 0
        changed = service.change_reading_preference(
            identity_id="alice", library_key="book:0",
            preference="liked", expected_revision=0, user_confirmed=True,
        )
        assert changed.revision == 1
        # The default ranking path must not consume persisted preference.
        neutral, _ = _rank(metadata, service, optin=False)
        assert neutral.ranked and calls[-1] is None
        assert [(r.item.title, r.score) for r in neutral.ranked] == [
            (r.item.title, r.score) for r in baseline.ranked
        ]
        saved, parts = _rank(metadata, service, optin=True)
        assert saved.ranked and parts and not saved.cancelled
        assert calls[-1] == (ExplicitReadingSignal(
            identity_id="alice", library_key="book:0", preference="liked",
        ),)
        for row in saved.ranked:
            assert row.item.metadata == {"catalogue_access": "shared_read_only", **({"language": "fr"} if provider == "paris" else {})}
            assert row.item.owned is False and row.item.downloadable is False
            assert row.item.download_url is None
        if provider != "paris":
            assert session.private_calls == 0

        erased = service.change_reading_preference(
            identity_id="alice", library_key="book:0",
            preference=None, expected_revision=1, user_confirmed=True,
        )
        assert erased.preferences == () and erased.revision == 2
        reset, _ = _rank(metadata, service, optin=True)
        assert reset.ranked and calls[-1] == ()
        assert [(r.item.title, r.score) for r in reset.ranked] == [
            (r.item.title, r.score) for r in baseline.ranked
        ]
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["paris", "numilog", "kobo"])
def test_saved_feedback_revision_invalidates_inflight_ranking_before_partial(
    tmp_path, monkeypatch, provider,
):
    service, session, metadata = _setup(tmp_path, monkeypatch, provider)
    try:
        service.change_reading_preference(
            identity_id="alice", library_key="book:0",
            preference="liked", expected_revision=0, user_confirmed=True,
        )
        # Atomic second user action during the provider call changes the
        # persisted preference generation before any ranking is published.
        session.mutate = lambda: change_preference(
            service.storage, "alice", library_key="book:0",
            preference="not_interested", expected_revision=1,
            user_confirmed=True,
        )
        cancelled, partials = _rank(metadata, service, optin=True)
        assert cancelled.cancelled
        assert cancelled.ranked == ()
        assert partials == []
        assert service.reading_preference_snapshot(identity_id="alice").revision == 2
        if provider != "paris":
            assert session.private_calls == 0
        session.mutate = None if provider == "paris" else lambda: None
        resumed, partials = _rank(metadata, service, optin=True)
        assert resumed.ranked and partials and not resumed.cancelled
    finally:
        service.close()


def test_explicit_preference_reader_is_optional_but_never_silently_assumed(
    tmp_path, monkeypatch,
):
    service, session, metadata = _setup(tmp_path, monkeypatch, "numilog")
    try:
        # Legacy metadata brokers have no preference API; neutral operation
        # remains completely unchanged. Opt-in without an owner fails closed.
        neutral, _ = _rank(_Metadata(), service, optin=False)
        assert neutral.ranked
        with pytest.raises(ValueError, match="reading_preference_broker_unavailable"):
            _rank(_Metadata(), service, optin=True)
        with pytest.raises(ValueError, match="reading_preference_modes_conflict"):
            recommendation_service.compute_recommendations(
                metadata, service, "alice", max_queries=1, exploration=0,
                use_saved_explicit_preferences=True,
                explicit_signals=(ExplicitReadingSignal("alice", "book:0", "liked"),),
            )
        assert session.private_calls == 0
    finally:
        service.close()
