"""#3416: typed, provider-neutral informative inventory partial in CoreUI.

Headless GTK rendering only; no provider network, consent mutation, file writes
or second acquisition path.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_view import (
    sync_source_attention, sync_source_informational_partial,
    sync_source_presentation,
)
from tests.test_core_ui_1347_sync_action_scope import _fixture, _source


def source(*, provider="provider.reference", materialize="success", **changes):
    facts = dict(
        provider_id=provider, source_id=provider, label="Compte de test",
        status="partial", error="Synchronisation partielle",
        phase_states=(
            NS(phase="discover_rights", status="partial"),
            NS(phase="materialize", status=materialize),
        ),
        sync_capabilities=("remote_inventory", "local_materialization"),
    )
    facts.update(changes)
    return _source(**facts)


@pytest.mark.parametrize("provider", [
    "provider.reference", "numilog", "kobo", "paris", "bnf-pret", "other.future",
])
@pytest.mark.parametrize("materialize", ["success", "not_supported"])
def test_inventory_coverage_partial_is_informational_with_explicit_local_phase(
    provider, materialize,
):
    item = source(provider=provider, materialize=materialize)
    assert sync_source_informational_partial(item) is True
    assert sync_source_attention(item) == (
        "info", "Notices et droits : couverture partielle (information)"
    )
    presented = sync_source_presentation(item)
    assert presented.sync_label == "Partielle"
    assert "aucun échec de téléchargement local déclaré" in presented.summary
    assert "publication des fichiers restent distinctes" in presented.summary


@pytest.mark.parametrize("local_status", [
    "partial", "failed", "blocked", "interrupted", "unknown", "idle",
])
def test_nonpositive_local_status_is_never_downgraded_to_information(local_status):
    item = source(materialize=local_status)
    assert not sync_source_informational_partial(item)
    alert = sync_source_attention(item)
    assert alert is not None
    assert alert[0] in {"error", "warning"}
    assert "aucun échec de téléchargement local déclaré" not in (
        sync_source_presentation(item).summary
    )


@pytest.mark.parametrize("bad_data", [
    {"error": "SECRET?token=PRIVATE"},
    {"error": "Synchronisation partielle\nSECRET"},
    {"status": "failed"},
    {"phase_states": (NS(phase="discover_rights", status="partial"),)},
    {"phase_states": (NS(phase="discover_rights", status="partial"),
                      NS(phase="materialize", status="success"),
                      NS(phase="materialize", status="failed"))},
    {"phase_states": (NS(phase="discover_rights", status="partial"),
                      NS(phase="foreign", status="success"))},
    {"sync_capable": False},
])
def test_information_requires_closed_exhaustive_evidence(bad_data):
    item = source(**bad_data)
    assert not sync_source_informational_partial(item)
    alert = sync_source_attention(item)
    assert alert is None or alert[0] != "info"


def test_informational_partial_uses_neutral_header_and_does_not_raise_attention_count():
    item = source(provider="provider.future")
    window, _broker, calls, _build, _page = _fixture(sources=[item])
    row = window._sync_sources_box.children[0].children[0]
    assert row.subtitle.startswith(
        "Notices et droits : couverture partielle (information)\n"
    )
    assert row.prefixes[0].icon_name == "dialog-information-symbolic"
    assert "info" in row.prefixes[0].css_classes
    assert window._sync_attention_icon.icon_name == "dialog-information-symbolic"
    assert window._sync_attention_text.label == (
        "Information : 1 source(s) à couverture partielle ; aucun échec local déclaré."
    )
    assert window._sync_attention_row.visible is True
    assert not window.runner.tasks
    assert all(call[0] == "sources" for call in calls)


def test_unknown_partial_still_warns_and_source_error_still_wins_over_information():
    pending = source(provider="provider.unknown", phase_states=(
        NS(phase="discover_rights", status="partial"),
    ))
    broken = source(provider="provider.failed", status="failed", error="PRIVATE-SECRET")
    good = source(provider="provider.informative")
    window, _, _calls, _, _ = _fixture(sources=[pending, broken, good])
    assert window._sync_attention_icon.icon_name == "dialog-error-symbolic"
    label = window._sync_attention_text.label
    assert "1 échec(s)" in label
    assert "1 avertissement(s)" in label
    assert "1 couverture(s) partielle(s) informative(s)" in label
    assert "PRIVATE-SECRET" not in label
    assert window._sync_sources_box.children[1].children[0].prefixes[0].icon_name == (
        "dialog-error-symbolic"
    )
    assert not window.runner.tasks


@pytest.mark.parametrize("state", ["partial", "running", "failed", "error"])
def test_actual_broker_error_precedes_partial_or_running_status_label(state):
    item = source(status=state, error="PRIVATE_TOKEN_DO_NOT_SHOW")
    presentation = sync_source_presentation(item)
    assert presentation.sync_label == "Échec"
    assert "PRIVATE_TOKEN_DO_NOT_SHOW" not in presentation.summary
    alert = sync_source_attention(item)
    assert alert is not None and alert[0] == "error"


def test_closed_partial_marker_still_displays_partial_without_declaring_failure():
    item = source(status="partial", error="Synchronisation partielle")
    assert sync_source_presentation(item).sync_label == "Partielle"
    assert sync_source_attention(item)[0] == "info"


def test_error_label_and_header_attention_are_consistent_for_unknown_provider():
    item = source(provider="provider.future", error="SECRET?token=PRIVATE")
    window, broker, calls, _, _ = _fixture(sources=[item])
    row = window._sync_sources_box.children[0].children[0]
    assert "Synchronisation : Échec" in row.subtitle
    assert row.prefixes[0].icon_name == "dialog-error-symbolic"
    assert "PRIVATE" not in row.subtitle
    assert window.runner.tasks == []
