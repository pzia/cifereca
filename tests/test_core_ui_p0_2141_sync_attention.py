"""#2141: source-level warnings visible without opening account tools."""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_view import sync_source_attention
from tests.test_core_ui_1347_sync_action_scope import _fixture, _source


@pytest.mark.parametrize(("status", "expected"), [
    ("failed", ("error", "Synchronisation : échec — action nécessaire")),
    ("error", ("error", "Synchronisation : échec — action nécessaire")),
    ("partial", ("warning", "Synchronisation : résultat partiel")),
    ("blocked", ("warning", "Synchronisation : action nécessaire")),
])
def test_source_alert_uses_only_closed_statuses(status, expected):
    source = _source(status=status, error="https://secret/?token=PRIVATE_TOKEN")
    assert sync_source_attention(source) == ("error", "Synchronisation : échec — action nécessaire")
    source.error = ""
    assert sync_source_attention(source) == expected


def test_failed_local_phase_not_concealed_by_successful_inventory():
    source = _source(
        provider_id="kobo", status="success", last_success="2026-10-20T10:00:00Z",
        phase_states=(
            NS(phase="discover_rights", status="success"),
            NS(phase="materialize", status="failed", error="PRIVATE_ACSM"),
        ),
    )
    assert sync_source_attention(source) == (
        "error", "Téléchargement local : échec — action nécessaire",
    )


def test_partial_or_interrupted_phase_remains_distinct():
    for code, text in (("interrupted", "Téléchargement local : interrompu"),
                       ("partial", "Téléchargement local : résultat partiel"),
                       ("attention_required", "Téléchargement local : action nécessaire")):
        source = _source(status="success", phase_states=(
            NS(phase="materialize", status=code),))
        assert sync_source_attention(source) == ("warning", text)


def test_no_alert_from_arbitrary_provider_text_or_public_only_sources():
    secret = "PRIVATE_BEARER_2141"
    assert sync_source_attention(_source(
        sync_capable=False, status="failed", error=secret)) is None
    assert sync_source_attention(_source(
        status="success", error="", phase_states=(
            NS(phase="PRIVATE_" + secret, status="failed"),
            NS(phase="materialize", status="PRIVATE_" + secret),
        ))) is None
    assert sync_source_attention(_source(status="success", error="")) is None


def test_account_card_displays_prominent_private_safe_status_without_extra_io():
    sources = [
        _source(provider_id="kobo", source_id="healthy", label="Kobo sain"),
        _source(provider_id="paris", source_id="failure", label="Paris en échec",
                status="failed", error="https://PRIVATE/?token=SECRET"),
        _source(provider_id="numilog", source_id="phase", label="Achats",
                status="success", phase_states=(
                    NS(phase="materialize", status="blocked", error="SECRET"),),
                sync_capabilities=("remote_inventory", "entitlements")),
        _source(provider_id="gutenberg", source_id="public", label="Catalogue",
                sync_capable=False, enabled=True, status="failed", error="SECRET"),
    ]
    window, _, calls, _, _ = _fixture(sources=sources)
    groups = {group._cifereca_sync_source_id: group
              for group in window._sync_sources_box.children}
    assert len(groups["healthy"].children[0].prefixes) == 0
    alert_row = groups["failure"].children[0]
    assert alert_row.subtitle.startswith("Synchronisation : échec — action nécessaire\n")
    assert "SECRET" not in alert_row.subtitle
    assert len(alert_row.prefixes) == 1
    assert alert_row.prefixes[0].icon_name == "dialog-error-symbolic"
    assert "error" in alert_row.prefixes[0].css_classes
    assert groups["phase"].children[0].subtitle.startswith(
        "Téléchargement local : bloqué — action nécessaire\n"
    )
    assert groups["public"].children[0].prefixes == []
    assert len(groups["public"].children[0].suffixes) == 1
    assert not window.runner.tasks
    assert all(entry[0] == "sources" for entry in calls)
    window._refresh_sync_page()
    groups = {group._cifereca_sync_source_id: group
              for group in window._sync_sources_box.children}
    assert len(groups["failure"].children[0].prefixes) == 1
    assert not window.runner.tasks
