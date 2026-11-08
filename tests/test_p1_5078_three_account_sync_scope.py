"""#5078: 3 independently scoped Kobo sources, old W2 checkpoints vs Core cycle.

No provider, credential, HTTP, acquisition, consent change, or cache mutation.
The actual Sync controller is driven through its established AST widget harness,
not merely a standalone formatting helper.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from cifereca.core.diagnostic_source_sync import CANONICAL_EXECUTION_STATUSES
from cifereca.gui.sync_view import (
    sync_source_attention, sync_source_phase_summary, sync_source_presentation,
)
from cifereca.plugin_api import SyncPhaseState, SyncSourceCycleReceipt, SyncSourceRef
from tests.test_core_ui_1347_sync_action_scope import _fixture


CYCLE = "2026-11-08T10:36:54+00:00"
OLD = "2026-10-30T09:58:30+00:00"
EARLIER = "2026-10-15T10:00:00+00:00"


def canonical_receipt(*, status="success", counts=None, verified=True):
    data = dict.fromkeys(CANONICAL_EXECUTION_STATUSES, 0)
    data.update(present_attested=517, skipped_archived=5)
    if counts:
        data.update(counts)
    return SyncSourceCycleReceipt(
        status=status, attempted_at=CYCLE, mode="manual",
        canonical_execution=(
            tuple((key, data[key]) for key in CANONICAL_EXECUTION_STATUSES)
            if verified else None
        ),
    )


def scoped_three_sources():
    """9 / 522 / 5 Kobo historical scopes with only the middle success current."""
    return (
        SyncSourceRef(
            "kobo-local-9", "alice", "kobo", "synthetic-9", "Kobo 9 livres",
            status="idle", sync_capabilities=("local_materialization",),
            phase_states=(
                SyncPhaseState("discover_rights", "idle"),
                SyncPhaseState("materialize", "attention_required",
                               last_attempt=EARLIER),
            ),
        ),
        SyncSourceRef(
            "kobo-local-522", "alice", "kobo", "synthetic-522", "Kobo 522 livres",
            status="success", last_attempt=CYCLE, last_success=CYCLE,
            sync_capabilities=("remote_inventory", "entitlements",
                               "local_materialization"),
            phase_states=(
                SyncPhaseState("discover_rights", "success",
                               last_attempt=CYCLE, last_success=CYCLE),
                SyncPhaseState("materialize", "partial", last_attempt=OLD),
                SyncPhaseState("reconcile", "not_supported"),
            ),
            last_cycle=canonical_receipt(),
        ),
        SyncSourceRef(
            "kobo-local-5", "alice", "kobo", "synthetic-5", "Kobo 5 livres",
            status="idle", sync_capabilities=("local_materialization",),
            phase_states=(
                SyncPhaseState("discover_rights", "idle"),
                SyncPhaseState("materialize", "attention_required",
                               last_attempt=EARLIER),
            ),
        ),
    )


def _source_rows(window):
    return [
        group.children[0] for group in window._sync_sources_box.children
        if group.children and getattr(group.children[0], "title", None)
    ]


def test_9_522_5_checkpoint_histories_retain_two_warnings_not_one_fake_failure():
    sources = scoped_three_sources()
    assert tuple(row.source_id for row in sources) == (
        "kobo-local-9", "kobo-local-522", "kobo-local-5",
    )
    assert sum(sync_source_attention(s) is not None for s in sources) == 2
    assert sync_source_attention(sources[1]) is None
    presentation = sync_source_presentation(sources[1])
    assert "Dernier succès 08/11/2026 11:36" in presentation.sync_label
    assert "Partielle (historique, antérieur au dernier cycle canonique)" in presentation.summary
    assert "30/10/2026" in presentation.summary
    assert "Téléchargement local : Réussie" not in presentation.summary
    assert "État à vérifier" not in presentation.summary
    window, broker, calls, _build, _page = _fixture(
        identity_id="alice", sources=sources,
    )
    rows = _source_rows(window)
    assert len(rows) == 3
    subtitles = {row.title: row.subtitle for row in rows}
    assert all(title in subtitles for title in (
        "Kobo 9 livres", "Kobo 522 livres", "Kobo 5 livres",
    ))
    assert "Synchronisation : Dernier succès" in subtitles["Kobo 522 livres"]
    assert "historique, antérieur" in subtitles["Kobo 522 livres"]
    assert "État à vérifier" not in subtitles["Kobo 522 livres"]
    assert window._sync_attention_row.visible
    assert "2 avertissement(s)" in window._sync_attention_text.label
    assert window.runner.tasks == []
    assert not any(str(name).startswith("sync") for name, *_ in calls)


@pytest.mark.parametrize(("changes", "expected"), [
    ({"last_cycle": None}, ("warning", "Téléchargement local : résultat partiel")),
    ({"last_cycle": canonical_receipt(status="partial")},
     ("warning", "Téléchargement local : résultat partiel")),
    ({"last_cycle": canonical_receipt(counts={"failed": 1})},
     ("warning", "Téléchargement local : résultat partiel")),
    ({"last_cycle": canonical_receipt(counts={"pending": 1})},
     ("warning", "Téléchargement local : résultat partiel")),
    ({"last_cycle": canonical_receipt(verified=False)},
     ("warning", "Téléchargement local : résultat partiel")),
    ({"status": "partial"}, ("warning", "Téléchargement local : résultat partiel")),
    ({"phase_states": (
        SyncPhaseState("discover_rights", "success", last_attempt=CYCLE),
        SyncPhaseState("materialize", "failed",
                       last_attempt="2026-11-08T10:37:30+00:00"),
        SyncPhaseState("reconcile", "not_supported"),
    )}, ("error", "Téléchargement local : échec — action nécessaire")),
])
def test_active_or_unproven_failure_remains_alert_even_after_older_success(changes, expected):
    source = replace(scoped_three_sources()[1], **changes)
    assert sync_source_attention(source) == expected
    assert "historique, antérieur" not in sync_source_phase_summary(source)


def test_other_account_and_identity_do_not_inherit_neighbor_success():
    first, successful, third = scoped_three_sources()
    foreign = replace(
        first, source_id="kobo-local-522-other",
        identity_id="bob", account_id="synthetic-522",
        phase_states=successful.phase_states,
        status="success", last_attempt=successful.last_attempt,
        last_success=successful.last_success, last_cycle=None,
    )
    assert sync_source_attention(foreign) == (
        "warning", "Téléchargement local : résultat partiel",
    )
    assert sync_source_attention(successful) is None
    window, broker, calls, *_ = _fixture(
        identity_id="alice", sources=(first, successful, third, foreign),
    )
    assert len(_source_rows(window)) == 3
    assert "3 source(s)" in window._sync_summary.label
    assert window.runner.tasks == []
