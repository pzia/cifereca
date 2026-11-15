"""P1 #6168 N4: exact Search holdings-to-list Core N2 projection."""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.search_cover_bridge import SearchCoverBridge
from tests.test_core_p1_3104_search_detail_cover_live_row import (
    COVER, detail, host, item, publisher,
)


CONTEXT = (9, 3, 7, 4)
IDENTITY = "alice"
ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"


def test_exact_search_row_rebuilt_from_same_notice_reuses_core_decision():
    source = item()
    bridge = SearchCoverBridge()
    assert bridge.selected_url(source, identity_id=IDENTITY, context=CONTEXT) is None
    assert bridge.publish(
        source, detail(source), identity_id=IDENTITY, context=CONTEXT,
    )
    reconstructed = replace(source)
    assert reconstructed is not source
    assert bridge.selected_url(
        reconstructed, identity_id=IDENTITY, context=CONTEXT,
    ) == COVER
    assert source.thumbnail_url is None
    assert bridge.stats() == {"scopes": 1}


@pytest.mark.parametrize("changed", [
    {"source_provider": "paris"}, {"source_credential": "card-b"},
    {"record_id": "other-edition"}, {"docbase": "OTHER"},
    {"source_collection": "another-collection"},
    {"source_account_id": "another-account"},
    {"revision_id": "new-edition-revision"},
])
def test_same_title_and_record_display_never_cross_source(changed):
    source = item()
    bridge = SearchCoverBridge()
    assert bridge.publish(
        source, detail(source), identity_id=IDENTITY, context=CONTEXT,
    )
    other = replace(source, **changed)
    assert other.title == source.title
    assert bridge.selected_url(
        other, identity_id=IDENTITY, context=CONTEXT,
    ) is None


@pytest.mark.parametrize("context", [
    (10, 3, 7, 4), (9, 4, 7, 4),
    (9, 3, 8, 4), (9, 3, 7, 5),
])
def test_context_change_never_reuses_previous_cover(context):
    source = item()
    bridge = SearchCoverBridge()
    assert bridge.publish(
        source, detail(source), identity_id=IDENTITY, context=CONTEXT,
    )
    assert bridge.selected_url(
        source, identity_id=IDENTITY, context=context,
    ) is None


def test_other_profile_is_not_covered_even_after_aba_to_same_source_key():
    source = item()
    bridge = SearchCoverBridge()
    assert bridge.publish(
        source, detail(source), identity_id="alice", context=CONTEXT,
    )
    assert bridge.selected_url(source, identity_id="bob", context=CONTEXT) is None
    assert bridge.selected_url(
        source, identity_id="alice", context=(10, 3, 7, 4),
    ) is None


@pytest.mark.parametrize("mutated", [
    {"provider": "other"}, {"credential": "foreign"}, {"record": "other"},
    {"url": "https://cdn.example/cover.png?token=secret"},
    {"url": "https://127.0.0.1/private.png"},
    {"url": "file:///home/alice/private.png"},
])
def test_invalid_detail_or_private_url_never_stored(mutated):
    source = item()
    bridge = SearchCoverBridge()
    assert not bridge.publish(
        source, detail(source, **mutated),
        identity_id=IDENTITY, context=CONTEXT,
    )
    assert bridge.selected_url(
        source, identity_id=IDENTITY, context=CONTEXT,
    ) is None


def test_failed_late_refresh_preserves_existing_good_decision():
    source = item()
    bridge = SearchCoverBridge()
    assert bridge.publish(
        source, detail(source), identity_id=IDENTITY, context=CONTEXT,
    )
    assert not bridge.publish(
        source, detail(source, url="https://cdn.example/?token=secret"),
        identity_id=IDENTITY, context=CONTEXT,
    )
    assert bridge.selected_url(
        source, identity_id=IDENTITY, context=CONTEXT,
    ) == COVER


def test_new_attested_cover_replaces_previous_same_source_decision():
    source = item()
    bridge = SearchCoverBridge()
    assert bridge.publish(
        source, detail(source), identity_id=IDENTITY, context=CONTEXT,
    )
    newer = "https://covers.example.org/next.jpg"
    assert bridge.publish(
        source, detail(source, url=newer),
        identity_id=IDENTITY, context=CONTEXT,
    )
    assert bridge.selected_url(
        source, identity_id=IDENTITY, context=CONTEXT,
    ) == newer


def test_invalidation_revokes_only_exact_source():
    source = item()
    other = item(credential="card-b")
    bridge = SearchCoverBridge()
    for candidate in (source, other):
        assert bridge.publish(
            candidate, detail(candidate), identity_id=IDENTITY, context=CONTEXT,
        )
    bridge.invalidate(source, identity_id=IDENTITY)
    assert bridge.selected_url(
        source, identity_id=IDENTITY, context=CONTEXT,
    ) is None
    assert bridge.selected_url(
        other, identity_id=IDENTITY, context=CONTEXT,
    ) == COVER


def test_bound_lru_drops_old_sources_without_replay():
    bridge = SearchCoverBridge(max_sources=2)
    variants = [item(record="edition-" + str(i)) for i in range(3)]
    for source in variants:
        assert bridge.publish(
            source, detail(source),
            identity_id=IDENTITY, context=CONTEXT,
        )
    assert bridge.stats() == {"scopes": 2}
    assert bridge.selected_url(
        variants[0], identity_id=IDENTITY, context=CONTEXT,
    ) is None
    assert bridge.selected_url(
        variants[-1], identity_id=IDENTITY, context=CONTEXT,
    ) == COVER


@pytest.mark.parametrize("bad", [
    (), (9, 3, 7), (9, 3, 7, -1), (9, 3, True, 4),
    ("9", 3, 7, 4), [9, 3, 7, 4],
])
def test_malformed_search_context_cannot_be_admitted(bad):
    source = item()
    bridge = SearchCoverBridge()
    assert not bridge.publish(
        source, detail(source), identity_id=IDENTITY, context=bad,
    )
    assert bridge.selected_url(
        source, identity_id=IDENTITY, context=bad,
    ) is None


def test_selected_row_publisher_also_stages_same_notice_core_decision():
    source = item()
    window, row = host(source)
    window.service.current_identity = NS(identity_id="alice")
    publisher()(window, source, detail(source))
    assert row.changes == [COVER]
    assert window._search_cover_bridge.selected_url(
        replace(source), identity_id="alice", context=CONTEXT,
    ) == COVER
    assert source.thumbnail_url is None
    # A second source object with the same book title but another credential
    # remains unadmitted to this exact decision.
    assert window._search_cover_bridge.selected_url(
        item(credential="card-b"), identity_id="alice", context=CONTEXT,
    ) is None


def test_search_result_renderer_consumes_exact_cache_after_legacy_fast_path():
    source = (ROOT / "search_results.py").read_text(encoding="utf-8")
    assert "bridge.selected_url(" in source
    assert "identity_id=identity_id" in source
    assert 'cover_url = resolved_uri' in source
    assert 'ui_rows.append(BookListItem(' in source
    detail_src = (ROOT / "search_details.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(detail_src).body
                if isinstance(n, ast.FunctionDef)
                and n.name == "_publish_search_detail_cover")
    actual = ast.unparse(node)
    assert "row.set_thumbnail_url(cover.url)" in actual
    assert "bridge.publish(" in actual
    assert "self._render_search_results()" not in actual
    port = (ROOT / "search_host_port.py").read_text(encoding="utf-8")
    assert '"_search_cover_bridge"' in port
