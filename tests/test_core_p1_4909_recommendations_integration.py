"""P1 #4909: Recommendations consumes Core's shared bibliographic projection.

This test connects the *real* Recommendations ranking pipeline to the Core
progressive catalogue broker. It never calls Paris/Numilog/Kobo HTTP: the
provider sessions are deterministic, deliberately rights-bearing fakes.
"""
from dataclasses import replace
from pathlib import Path
import sys

# The integration intentionally exercises both Core and plugin code. Plugin
# unit tests run in isolated package scope and must not import Core fixtures.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "plugins" / "recommendations"))

import pytest

from recommendation_service import compute_recommendations

from tests.test_core_p0_4673_paris_foreign_search import _fixture as paris_setup
from tests.test_core_p1_4909_numilog_shared_public import setup as numilog_setup
from tests.test_core_p1_4909_kobo_shared_public import setup as kobo_setup


class _Metadata:
    def library_entries(self, *, identity_id, scan):
        assert identity_id in {"alice", "bob"} and scan is False
        return [
            {"title": "Seed 1", "author": "Alice",
             "metadata": {"subjects": ["histoire"]}},
            {"title": "Seed 2", "author": "Alice",
             "metadata": {"subjects": ["mémoire"]}},
        ]


def _recommend(service, identity):
    assert service.current_identity.identity_id == identity
    partials = []
    result = compute_recommendations(
        _Metadata(), service, identity,
        max_queries=1, exploration=0, exclude_owned=False,
        partial=partials.append,
    )
    assert not result.cancelled and len(result.queries) == 1
    return result, partials


@pytest.mark.parametrize("provider", ["paris", "numilog", "kobo"])
def test_shared_bibliography_survives_recommendations_ranking_without_b_rights(
    tmp_path, monkeypatch, provider,
):
    if provider == "paris":
        service, _alice, session, _public, _owners = paris_setup(tmp_path, monkeypatch)
    elif provider == "numilog":
        service, session, _owners = numilog_setup(tmp_path, monkeypatch)
    else:
        service, session, _owners = kobo_setup(tmp_path, monkeypatch)

    service._sessions = {"bob": session}
    # Make the synthetic bibliographic candidate match the profile author:
    # ranking then has a positive, deterministic content score.
    original_search = session.search

    def matched_search(query, scope, **kwargs):
        return [
            replace(row, author="Alice")
            for row in original_search(query, scope, **kwargs)
        ]

    session.search = matched_search

    def shared_only(rows):
        assert rows
        for ranked in rows:
            item = ranked.item
            assert item.source_provider == provider
            assert item.owned is False
            assert item.downloadable is False
            assert item.download_url is None
            assert item.friendly_url is None
            assert item.thumbnail_url is None
            assert item.metadata == {"catalogue_access": "shared_read_only"}
            assert ranked.sources == (provider,)
            assert ranked.notices
            expected_credential = "__public__" if provider == "numilog" else session.credential_id
            assert all(
                notice.source_credential == expected_credential
                for notice in ranked.notices
            )

    try:
        a, partials = _recommend(service, "alice")
        assert a.providers and a.providers[0][0] == provider
        assert a.ranked and partials
        shared_only(a.ranked)
        for partial in partials:
            shared_only(partial.ranked)
        if provider != "paris":
            assert session.private_calls == 0
            assert session.public_calls > 0
        else:
            assert session.calls > 0

        # B can still use the personal library/rights-bearing search route,
        # but A must not inherit its flags or cache on the return journey.
        service.select_identity("bob")
        b, _ = _recommend(service, "bob")
        assert b.ranked
        assert any(row.item.owned for row in b.ranked)

        service.select_identity("alice")
        a_again, _ = _recommend(service, "alice")
        shared_only(a_again.ranked)

        # Recommendations' source inventory and ranked rows both drop a
        # disconnected B; a stale cached shared candidate is not reused.
        session.connected = False
        disconnected, events = _recommend(service, "alice")
        assert disconnected.providers == ()
        assert disconnected.ranked == ()
        assert not events
        session.connected = True
        recovered, _ = _recommend(service, "alice")
        shared_only(recovered.ranked)
        if provider != "paris":
            assert session.private_calls >= 1  # only while Bob was selected
    finally:
        service.close()
