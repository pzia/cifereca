"""#3416: UI-only account labels are not a credential or signed-URL channel."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_view import (
    sync_source_identity_label, sync_source_presentation, sync_source_row_labels,
)
from tests.test_core_ui_1347_sync_action_scope import _fixture, _source


@pytest.mark.parametrize("private", [
    "https://private.invalid/epub?token=PRIVATE_SIGNED",
    "PRIVATE_LOGIN\nBearer PRIVATE_SECRET",
    "file:///home/PRIVATE_OWNER/library",
    "/home/PRIVATE_OWNER/.config/private",
    "PRIVATE" * 30,
    "Compte?access_token=PRIVATE_KEY",
    42, False, None, ["PRIVATE_ARRAY"],
])
def test_malformed_source_label_never_reaches_sync_titles_or_task(private):
    item = _source(
        provider_id="plugin.future", source_id="opaque-id",
        label=private,
    )
    assert sync_source_identity_label(item) == "plugin.future"
    assert sync_source_row_labels([item]) == ("plugin.future",)
    projection = sync_source_presentation(item)
    assert projection.action_label == "Synchroniser plugin.future"
    assert "PRIVATE" not in projection.action_label
    window, broker, calls, _build, _page = _fixture(sources=[item])
    header = window._sync_sources_box.children[0].children[0]
    assert header.title == "plugin.future"
    assert header.suffixes[1].label == "Synchroniser plugin.future"
    header.suffixes[1].click()
    assert window.runner.tasks[0].label == "Synchronisation plugin.future…"
    assert "PRIVATE" not in repr(header.title) + repr(projection.summary)
    assert window.runner.tasks[0].work() == "ok"
    assert ("sync-source", "opaque-id") in calls


def test_safe_name_preserves_human_label_and_independent_scoped_route():
    items = [
        _source(provider_id="paris", source_id="opaque-a", label="Paris · Camille"),
        _source(provider_id="paris", source_id="opaque-b", label="Paris · Camille"),
    ]
    titles = sync_source_row_labels(items)
    assert titles == (
        "Paris · Camille · paris · connexion 1/2",
        "Paris · Camille · paris · connexion 2/2",
    )
    assert sync_source_presentation(items[0]).action_label == "Synchroniser Paris · Camille"


def test_secret_label_with_invalid_provider_uses_fixed_unknown_fallback():
    item = _source(
        provider_id="https://provider.invalid/?token=SECRET",
        label="https://private.invalid/?token=SECRET",
    )
    assert sync_source_identity_label(item) == "fournisseur inconnu"
    assert sync_source_presentation(item).action_label == "Synchroniser fournisseur inconnu"
    assert "SECRET" not in sync_source_presentation(item).summary
