"""#4681: optional documentary evidence scope never authorizes provider actions."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from recommendation_engine import ExplicitReadingSignal
from recommendation_service import compute_recommendations


def _books():
    return [
        {
            "key": "artifact:a", "identity_id": "alice",
            "title": "Locally owned", "author": "Auteur Local",
            "state": "local", "local": True,
            "artifact_id": "sha256:local", "placement_id": "p-a",
            "placements": [{
                "identity_id": "alice", "placement_id": "p-a",
                "state": "present",
            }],
            "metadata": {"subjects": ["histoire"]},
        },
        {
            "key": "remote:b", "identity_id": "alice",
            "title": "Remote acquisition", "author": "Auteur Distant",
            "state": "remote_loan_active", "local": False,
            "entitlement_type": "loan", "artifact_id": None,
            "placement_id": None, "placements": [],
            "metadata": {"subjects": ["voyage"]},
        },
    ]


def test_documentary_local_only_ranking_queries_are_voluntary_and_reversible():
    books = _books()
    metadata = Mock(library_entries=Mock(return_value=books))
    catalogue = Mock(searchable_providers=Mock(return_value=()))

    baseline = compute_recommendations(metadata, catalogue, "alice")
    local = compute_recommendations(
        metadata, catalogue, "alice",
        documentary_source_policy="attested_local",
    )
    again = compute_recommendations(metadata, catalogue, "alice")
    assert "Auteur Local" in baseline.queries
    assert "Auteur Distant" in baseline.queries
    assert "Auteur Local" in local.queries
    assert "Auteur Distant" not in local.queries
    assert baseline.queries == again.queries
    assert local.books_count == 2
    assert baseline.books_count == 2
    assert local.ranked == baseline.ranked == ()
    assert metadata.library_entries.call_count == 3
    metadata.library_entries.assert_called_with(
        identity_id="alice", scan=False,
    )
    assert catalogue.searchable_providers.call_count == 3
    assert not catalogue.search.called
    assert not catalogue.search_progressive.called


def test_explicit_liking_restores_remote_book_as_user_signal_not_provider_right():
    metadata = Mock(library_entries=Mock(return_value=_books()))
    catalogue = Mock(searchable_providers=Mock(return_value=()))
    result = compute_recommendations(
        metadata, catalogue, "alice",
        documentary_source_policy="attested_local",
        explicit_signals=(
            ExplicitReadingSignal("alice", "remote:b", "liked"),
        ),
    )
    assert "Auteur Distant" in result.queries
    assert result.ranked == ()
    assert not catalogue.search.called


def test_foreign_library_rows_cannot_seed_active_local_only_profile():
    books = _books()
    books[0]["identity_id"] = "bob"
    metadata = Mock(library_entries=Mock(return_value=books))
    catalogue = Mock(searchable_providers=Mock(return_value=()))
    local = compute_recommendations(
        metadata, catalogue, "alice",
        documentary_source_policy="attested_local",
    )
    assert "Auteur Local" not in local.queries
    assert "Auteur Distant" not in local.queries
    assert local.ranked == ()
    assert not catalogue.search.called


@pytest.mark.parametrize("mode", ["known_remote", True, None, "", 0])
def test_bad_documentary_mode_is_rejected_before_provider_discovery(mode):
    metadata = Mock(library_entries=Mock(return_value=_books()))
    catalogue = Mock(searchable_providers=Mock(return_value=()))
    with pytest.raises(ValueError, match="invalid_documentary_source_policy"):
        compute_recommendations(
            metadata, catalogue, "alice",
            documentary_source_policy=mode,
        )
    catalogue.searchable_providers.assert_not_called()
