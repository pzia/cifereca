"""#3416: urgent per-source actions are visible; detailed text is on hover.

Tests exercise the actual GTK controller AST with headless widget doubles.
No account, provider, network, source write, lease or acquisition is used.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_source_card_guidance import sync_source_card_guidance
from cifereca.gui.sync_view import sync_source_attention
from tests.test_core_ui_1347_sync_action_scope import (
    Widget, _fixture, _source,
)


@pytest.mark.parametrize(("fields", "expected"), [
    (dict(status="failed", connected=True, enabled=True),
     "examiner le dernier cycle"),
    (dict(status="failed", connected=False, enabled=True),
     "reconnecter ce compte"),
    (dict(status="partial", connected=True, enabled=True),
     "examiner le dernier cycle"),
])
def test_problem_guidance_is_source_scoped_and_actionable(fields, expected):
    source = _source(provider_id="future-provider", **fields)
    alert = sync_source_attention(source)
    assert alert is not None
    assert alert[0] in {"error", "warning"}
    hint = sync_source_card_guidance(source, alert)
    assert expected in hint
    assert "future-provider" not in hint
    assert "kobo" not in hint.casefold()


@pytest.mark.parametrize("source", [
    _source(status="idle"),
    _source(status="success"),
    _source(status="partial", enabled=False, sync_capable=False),
])
def test_no_problem_never_adds_fake_repair_action(source):
    assert sync_source_card_guidance(source, None) == ""
    assert sync_source_card_guidance(source, ("info", "information")) == ""


def test_explicit_off_hides_historical_blockage_from_current_attention():
    source = _source(
        provider_id="future-provider", enabled=False, connected=True,
        status="blocked",
    )
    assert sync_source_attention(source) is None
    assert sync_source_card_guidance(source, None) == ""
    window, _, _, _, _ = _fixture(sources=[source])
    row = window._sync_sources_box.children[0].children[0]
    assert row.subtitle.startswith("Synchronisation : désactivée · aucune synchronisation attendue")
    assert "Connexion : Connectée" in row.subtitle
    assert "activer la source" not in row.subtitle.casefold()
    assert "Synchronisation : Désactivée" in row.tooltip



def test_untrusted_alert_or_provider_text_never_becomes_advice():
    source = _source(
        provider_id="opaque-future",
        source_id="private://secret/token",
        error="https://private.invalid/?token=SECRET_CARD",
        status="failed",
    )
    for alert in ("error", ("error",), ("unsupported", "SECRET_CARD"), [], None):
        assert sync_source_card_guidance(source, alert) == ""
    hint = sync_source_card_guidance(source, sync_source_attention(source))
    assert "SECRET_CARD" not in hint
    assert "private.invalid" not in hint
    assert "private://" not in hint


@pytest.mark.parametrize(("options", "text"), [
    (dict(status="failed", error="https://private.invalid/SECRET_CARD"),
     "examiner le dernier cycle"),
    (dict(status="partial", error="Synchronisation partielle",
          phase_states=(NS(phase="discover_rights", status="partial"),)),
     "examiner le dernier cycle"),
    (dict(status="failed", enabled=True, connected=False),
     "reconnecter ce compte"),
])
def test_actual_gtk_card_keeps_problem_and_next_step_above_collapsed_details(
    options, text, monkeypatch,
):
    def record_lines(widget, value):
        widget.subtitle_lines = value
    monkeypatch.setattr(Widget, "set_subtitle_lines", record_lines)
    source = _source(provider_id="future-provider", source_id="synthetic-card",
                     label="Compte fictif", **options)
    window, _, calls, _, _ = _fixture(sources=[source])
    group = window._sync_sources_box.children[0]
    row = group.children[0]
    assert row.subtitle_lines == 2
    assert row.subtitle.startswith(sync_source_attention(source)[1] + "\n")
    assert text in row.subtitle.splitlines()[1]
    assert len(row.prefixes) == 1
    assert len(group.children) == 1
    assert row.tooltip.startswith("Fournisseur : future-provider")
    assert "Synchronisation : " in row.tooltip
    assert "SECRET_CARD" not in row.subtitle
    assert "SECRET_CARD" not in row.tooltip
    assert calls == [("sources", "alice")]
    assert window.runner.tasks == []


def test_healthy_card_is_compact_and_preserves_manual_sync_action(monkeypatch):
    monkeypatch.setattr(Widget, "set_subtitle_lines",
                        lambda widget, count: setattr(widget, "subtitle_lines", count))
    source = _source(provider_id="future-provider", source_id="synthetic-card",
                     label="Compte fictif", status="success", error="")
    window, _, calls, _, _ = _fixture(sources=[source])
    group = window._sync_sources_box.children[0]
    row = group.children[0]
    assert row.subtitle_lines == 2
    assert row.subtitle.startswith("Fournisseur : future-provider\n")
    assert "À faire :" not in row.subtitle
    assert row.prefixes == []
    assert len(group.children) == 1
    assert row.tooltip.startswith("Fournisseur : future-provider")
    # The existing source action is still available, but no page rendering
    # starts it or grants new permission.
    assert row.suffixes[1].label == "Synchroniser Compte fictif"
    assert calls == [("sources", "alice")]
    assert window.runner.tasks == []


@pytest.mark.parametrize("state", [
    "failed", "blocked", "attention_required", "partial",
])
def test_offer_incident_has_specific_non_authorizing_action(state):
    source = _source(
        provider_id="future-provider", status="partial",
        error="Synchronisation partielle",
        phase_states=(
            NS(phase="discover_rights", status="success"),
            NS(phase="offer", status=state),
        ),
    )
    alert = sync_source_attention(source)
    assert alert is not None and alert[0] in {"error", "warning"}
    assert alert[1].startswith("Offre de livraison :")
    advice = sync_source_card_guidance(source, alert)
    assert advice == (
        "À faire : examiner la qualification des offres du dernier cycle."
    )
    assert "Télécharger" not in advice and "relancer" not in advice


def test_offer_guidance_respects_disabled_disconnected_and_unknown_account():
    def issue(**fields):
        return _source(
            provider_id="future-provider", status="partial",
            error="Synchronisation partielle",
            phase_states=(NS(phase="offer", status="failed"),),
            **fields,
        )
    for enabled, connected, expected in (
        (False, True, "activer la source si souhaité"),
        (True, False, "reconnecter ce compte"),
        (None, True, "vérifier l’activation"),
        (True, None, "vérifier la connexion"),
    ):
        source = issue(enabled=enabled, connected=connected)
        alert = sync_source_attention(source)
        hint = sync_source_card_guidance(
            source, alert or ("error", "Offre de livraison : échec — action nécessaire"),
        )
        assert expected in hint
        assert "qualification des offres" not in hint


def test_gtk_compact_card_shows_offer_action_without_background_mutation():
    source = _source(
        provider_id="future-provider", source_id="unknown-provider:account",
        label="Compte fictif", status="partial",
        error="Synchronisation partielle",
        phase_states=(NS(phase="offer", status="failed"),),
    )
    window, _, calls, _, _ = _fixture(sources=[source])
    row = window._sync_sources_box.children[0].children[0]
    assert row.subtitle.startswith("Offre de livraison : échec")
    assert "examiner la qualification des offres" in row.subtitle
    assert window.runner.tasks == []
    assert calls == [("sources", "alice")]


def test_information_only_partial_is_not_relabelled_as_user_problem():
    source = _source(
        provider_id="future-provider", status="partial",
        error="Synchronisation partielle",
        phase_states=(
            NS(phase="discover_rights", status="partial"),
            NS(phase="materialize", status="success"),
        ),
    )
    assert sync_source_attention(source) == (
        "info", "Notices et droits : couverture partielle (information)")
    assert sync_source_card_guidance(source, sync_source_attention(source)) == ""


def test_offline_disabled_card_keeps_connection_diagnostic_but_not_stale_failure():
    source = _source(
        provider_id="future-provider", enabled=False, connected=False,
        status="failed", error="PRIVATE_STALE_FAILURE",
    )
    window, _, _, _, _ = _fixture(sources=[source])
    row = window._sync_sources_box.children[0].children[0]
    assert row.subtitle.startswith(
        "Synchronisation : désactivée · aucune synchronisation attendue"
    )
    assert "Connexion : Déconnectée" in row.subtitle
    assert "PRIVATE_STALE_FAILURE" not in row.subtitle
    assert "Synchronisation : Désactivée" in row.tooltip
