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


@pytest.mark.parametrize(("phase_status", "source_status", "expected"), [
    ("partial", "failed", ("error", "Synchronisation : échec — action nécessaire")),
    ("interrupted", "error", ("error", "Synchronisation : échec — action nécessaire")),
    ("attention_required", "failed", ("error", "Synchronisation : échec — action nécessaire")),
    ("partial", "success", ("warning", "Téléchargement local : résultat partiel")),
])
def test_source_wide_failure_outranks_local_phase_warning(phase_status, source_status, expected):
    source = _source(status=source_status, error="", phase_states=(
        NS(phase="materialize", status=phase_status),))
    assert sync_source_attention(source) == expected


@pytest.mark.parametrize("reverse", [False, True])
def test_inventory_failure_outranks_materialization_warning_irrespective_of_phase_order(reverse):
    phases = [
        NS(phase="materialize", status="partial", error="PRIVATE_LOCAL"),
        NS(phase="discover_rights", status="failed", error="PRIVATE_INVENTORY"),
    ]
    source = _source(status="partial", error="", phase_states=(
        tuple(reversed(phases)) if reverse else tuple(phases)))
    assert sync_source_attention(source) == (
        "error", "Notices et droits : échec — action nécessaire",
    )


def test_local_error_remains_first_when_both_phases_fail_regardless_of_input_order():
    source = _source(status="failed", error="https://private.invalid/?token=SECRET",
                     phase_states=(
                         NS(phase="discover_rights", status="failed"),
                         NS(phase="materialize", status="blocked"),
                     ))
    assert sync_source_attention(source) == (
        "error", "Téléchargement local : bloqué — action nécessaire",
    )


def test_malformed_phase_collection_cannot_suppress_closed_source_failure():
    source = _source(status="failed", error="", phase_states=tuple(
        NS(phase="materialize", status="partial") for _ in range(9)))
    assert sync_source_attention(source) == (
        "error", "Synchronisation : échec — action nécessaire",
    )


@pytest.mark.parametrize("provider", ["bnf-pret", "paris"])
def test_p0_partial_loan_inventory_uses_warning_not_fatal(provider):
    # The broker's own compatibility marker accompanies a legitimate
    # source_sync_partial when HTML/legacy loans are not exhaustive.
    source = _source(
        provider_id=provider, status="partial",
        error="Synchronisation partielle", sync_capable=True,
        phase_states=(NS(phase="discover_rights", status="partial"),),
    )
    assert sync_source_attention(source) == (
        "warning", "Notices et droits : résultat partiel",
    )


@pytest.mark.parametrize("foreign_error", [
    "PRIVATE_URL/?token=secret", "authentication_failed",
    "synchronisation partielle", "Synchronisation partielle\nSECRET",
])
def test_p0_unknown_provider_error_still_overrides_partial_warning(foreign_error):
    source = _source(
        provider_id="bnf-pret", status="partial", error=foreign_error,
        phase_states=(NS(phase="discover_rights", status="partial"),),
    )
    assert sync_source_attention(source) == (
        "error", "Synchronisation : échec — action nécessaire",
    )
    assert "SECRET" not in sync_source_attention(source)[1]


def test_p0_fatal_phase_is_not_downgraded_by_core_partial_compatibility_marker():
    source = _source(
        provider_id="bnf-pret", status="partial",
        error="Synchronisation partielle",
        phase_states=(
            NS(phase="materialize", status="failed"),
            NS(phase="discover_rights", status="partial"),
        ),
    )
    assert sync_source_attention(source) == (
        "error", "Téléchargement local : échec — action nécessaire",
    )


def test_p0_partial_source_card_displays_warning_not_fatal_icon():
    # The headless broker double implements the generic Paris source row,
    # not the extra BnF auto-download toggle. Both providers exercise the
    # identical Core-owned partial marker in the projection tests above.
    source = _source(
        provider_id="paris", source_id="paris-partial",
        label="Prêts partiels", status="partial", sync_capable=True,
        error="Synchronisation partielle", phase_states=(
            NS(phase="discover_rights", status="partial"),
        ),
    )
    window, _, _, _, _ = _fixture(sources=[source])
    group = window._sync_sources_box.children[0]
    row = group.children[0]
    assert row.subtitle.startswith("Notices et droits : résultat partiel\n")
    assert len(row.prefixes) == 1
    assert row.prefixes[0].icon_name == "dialog-warning-symbolic"
