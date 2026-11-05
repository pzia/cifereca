"""#3416: two independent Sync controls visible without opening tools.

Synthetic broker DTOs only: no GUI session, provider calls, writes, or network.
"""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_source_controls_overview import sync_source_controls_overview


def source(**kwargs):
    values = dict(
        sync_capable=True, enabled=True, connected=True, source_id="opaque-source",
        sync_capabilities=(
            "remote_inventory", "scheduled_refresh", "local_materialization",
        ),
    )
    values.update(kwargs)
    return NS(**values)


def consent(**kwargs):
    values = dict(
        supported=True, configured=True, enabled=False,
        revision="revision-1", valid=True,
    )
    values.update(kwargs)
    return values


def broker(*, schedule=None, authorization=None, calls=None):
    calls = [] if calls is None else calls
    schedule = schedule if schedule is not None else dict(mode="manual", interval_minutes=60)
    authorization = authorization if authorization is not None else consent()
    return NS(
        inventory_refresh_schedule=lambda sid: (
            calls.append(("read-schedule", sid)) or schedule
        ),
        source_sync_consent=lambda sid: (
            calls.append(("read-consent", sid)) or authorization
        ),
    )


@pytest.mark.parametrize(("mode", "expected"), [
    ("manual", "manuelle"),
    ("on_start", "au démarrage"),
    ("periodic", "périodique"),
])
def test_two_controls_show_distinct_scopes_and_no_side_effect(mode, expected):
    calls = []
    info = sync_source_controls_overview(
        source(),
        broker(schedule=dict(mode=mode, interval_minutes=60),
               authorization=consent(enabled=True), calls=calls),
    )
    assert info == (
        f"Réglages · Notices et droits : {expected} · "
        "Fichiers : autorisation activée"
    )
    assert calls == [
        ("read-schedule", "opaque-source"),
        ("read-consent", "opaque-source"),
    ]
    assert "quota" not in info.lower()
    assert "restant" not in info.lower()


def test_off_and_unconfigured_consent_do_not_inherit_inventory_schedule():
    assert "Fichiers : autorisation désactivée" in sync_source_controls_overview(
        source(), broker(schedule=dict(mode="periodic", interval_minutes=120)),
    )
    info = sync_source_controls_overview(
        source(), broker(authorization=consent(
            configured=False, enabled=False, revision=None,
        )),
    )
    assert "Fichiers : choix non défini" in info
    assert "autorisation activée" not in info


def test_legacy_active_consent_stays_visible_after_capability_withdrawal():
    # The Core broker still allows explicit revocation of a prior ON policy.
    # An unsupported new activation cannot silently erase that persisted bit.
    info = sync_source_controls_overview(
        source(), broker(authorization=consent(supported=False, enabled=True)),
    )
    assert "Fichiers : autorisation antérieure activée (activation indisponible)" in info
    assert "Fichiers : autorisation non proposée" not in info
    off = sync_source_controls_overview(
        source(), broker(authorization=consent(supported=False, enabled=False)),
    )
    assert "autorisation antérieure désactivée" in off


def test_source_switch_does_not_erase_stored_independent_preferences():
    info = sync_source_controls_overview(
        source(enabled=False),
        broker(schedule=dict(mode="on_start", interval_minutes=60),
               authorization=consent(enabled=True)),
    )
    assert "Notices et droits : au démarrage" in info
    assert "Fichiers : autorisation activée" in info
    assert info.endswith("source désactivée")


def test_capability_scoping_and_unknown_provider_have_no_special_case():
    b = broker()
    assert sync_source_controls_overview(
        source(sync_capable=False, sync_capabilities=("remote_inventory",)), b
    ) == ""
    assert sync_source_controls_overview(
        source(sync_capabilities=("remote_inventory",)), b
    ) == "Réglages · Notices et droits : planification non proposée"
    assert sync_source_controls_overview(
        source(sync_capabilities=("local_materialization",)), b
    ) == "Réglages · Fichiers : autorisation désactivée"
    assert sync_source_controls_overview(
        source(sync_capabilities=("search",)), b
    ) == ""


@pytest.mark.parametrize("bad", [
    dict(mode="periodic", interval_minutes=True),
    dict(mode="auto", interval_minutes=15),
    dict(mode="manual", interval_minutes=0),
    dict(mode="manual", interval_minutes=10081),
    dict(mode="manual", interval_minutes=60, private="secret"),
    {"mode": "https://supplier.invalid/?token=SECRET", "interval_minutes": 20},
])
def test_bad_schedule_is_fail_closed_and_never_echoed(bad):
    info = sync_source_controls_overview(source(), broker(schedule=bad))
    assert "Notices et droits : réglage indisponible" in info
    assert "SECRET" not in info and "secret" not in info


@pytest.mark.parametrize("bad", [
    consent(enabled=True, configured=False, revision=None),
    consent(enabled=True, valid=False),
    consent(enabled="true"),
    consent(revision=""),
    consent(revision="x" * 129),
    consent(private="secret"),
    {"enabled": "https://invalid.test/?token=SECRET"},
])
def test_bad_consent_never_becomes_permission_or_exposes_provider_data(bad):
    info = sync_source_controls_overview(source(), broker(authorization=bad))
    assert "Fichiers : autorisation non vérifiée" in info
    assert "autorisation activée" not in info
    assert "SECRET" not in info and "secret" not in info


def test_read_failures_cannot_crash_sync_page_or_leak_exceptions():
    def fail(_):
        raise RuntimeError("https://private.invalid/?token=SECRET")
    b = NS(inventory_refresh_schedule=fail, source_sync_consent=fail)
    assert sync_source_controls_overview(source(), b) == (
        "Réglages · Notices et droits : réglage indisponible"
        " · Fichiers : autorisation non vérifiée"
    )


def test_controller_adds_overview_before_last_run_not_a_new_action():
    controller = (Path(__file__).resolve().parents[1] /
                  "src/cifereca/gui/sync_controller.py").read_text(encoding="utf-8")
    assert "sync_source_controls_overview(source, broker)" in controller
    assert 'head + "\\n" + control_overview' in controller


def test_gtk_card_keeps_provider_first_line_without_extra_broker_actions():
    from tests.test_core_ui_1347_sync_action_scope import _fixture, _source
    window, _, calls, _, _ = _fixture(sources=[
        _source(provider_id="future-provider", label="Compte fictif"),
    ])
    row = window._sync_sources_box.children[0].children[0]
    assert row.subtitle.startswith("Fournisseur : future-provider\n")
    assert "Réglages · Fichiers : autorisation non vérifiée" in row.subtitle
    assert not window.runner.tasks
    assert calls == [("sources", "alice")]


@pytest.mark.parametrize(("enabled", "connected", "reason"), [
    (True, False, "source déconnectée"),
    (False, True, "source désactivée"),
    (False, False, "source désactivée"),
])
def test_saved_on_modes_are_explicitly_suspended_when_source_unavailable(
    enabled, connected, reason,
):
    calls = []
    b = broker(
        schedule=dict(mode="periodic", interval_minutes=60),
        authorization=consent(enabled=True), calls=calls,
    )
    src = source(enabled=enabled, connected=connected)
    info = sync_source_controls_overview(src, b)
    assert "Notices et droits : périodique (suspendue)" in info
    assert "Fichiers : autorisation activée (suspendue)" in info
    assert reason in info
    assert "source déconnectée" in info if not connected else "source déconnectée" not in info
    assert calls == [
        ("read-schedule", "opaque-source"),
        ("read-consent", "opaque-source"),
    ]
    # The aggregate and each card must describe the same operational state.
    from cifereca.gui.sync_source_controls_overview import sync_axes_dashboard_summary
    overview = sync_axes_dashboard_summary([src], b)
    assert "0 programmation(s) active(s)" in overview
    assert "0 autorisation(s) effective(s)" in overview
    assert "1 programmation(s) suspendue(s)" in overview
    assert "1 autorisation(s) suspendue(s)" in overview
    assert "opaque-source" not in info and "opaque-source" not in overview


def test_manual_policy_is_not_counted_as_suspended_automatic_refresh():
    src = source(connected=False)
    info = sync_source_controls_overview(
        src, broker(
            schedule=dict(mode="manual", interval_minutes=60),
            authorization=consent(enabled=False),
        ),
    )
    assert "Notices et droits : manuelle" in info
    assert "manuelle (suspendue)" not in info
    assert "autorisation désactivée (suspendue)" not in info
    assert "source déconnectée" in info


def test_withdrawn_capability_with_old_on_consent_remains_visible_but_suspended():
    info = sync_source_controls_overview(
        source(connected=False),
        broker(
            schedule=dict(mode="on_start", interval_minutes=60),
            authorization=consent(supported=False, enabled=True),
        ),
    )
    assert "Notices et droits : au démarrage (suspendue)" in info
    assert "Fichiers : autorisation antérieure activée" in info
    assert "(activation indisponible)" in info
    assert "(suspendue)" in info
    assert "Fichiers : autorisation désactivée" not in info
    assert "source déconnectée" in info


def test_cannot_claim_effective_consent_when_connection_field_is_missing_or_truthy():
    for value in (None, 1, "true", object()):
        src = source(connected=value)
        info = sync_source_controls_overview(
            src,
            broker(schedule=dict(mode="periodic", interval_minutes=60),
                   authorization=consent(enabled=True)),
        )
        assert "périodique (effet non confirmé)" in info
        assert "autorisation activée (effet non confirmé)" in info
        assert "connexion de la source non confirmée" in info
        assert "source déconnectée" not in info


@pytest.mark.parametrize("flag", [None, "false", 0, 1, {}, []])
@pytest.mark.parametrize("field", ["enabled", "connected"])
def test_unknown_source_status_is_not_misrepresented_as_off_or_effective(
    flag, field,
):
    from cifereca.gui.sync_source_controls_overview import sync_axes_dashboard_summary
    src = source(**{field: flag})
    b = broker(
        schedule=dict(mode="on_start", interval_minutes=60),
        authorization=consent(enabled=True),
    )
    view = sync_source_controls_overview(src, b)
    assert "Notices et droits : au démarrage (effet non confirmé)" in view
    assert "Fichiers : autorisation activée (effet non confirmé)" in view
    assert "source désactivée" not in view
    assert "source déconnectée" not in view
    assert (
        "activation de la source non confirmée" if field == "enabled"
        else "connexion de la source non confirmée"
    ) in view
    dashboard = sync_axes_dashboard_summary([src], b)
    assert "0 programmation(s) active(s)" in dashboard
    assert "0 autorisation(s) effective(s)" in dashboard
    assert "0 programmation(s) suspendue(s)" not in dashboard
    assert "0 autorisation(s) suspendue(s)" not in dashboard
    assert "1 programmation(s) inconnue(s)" in dashboard
    assert "1 autorisation(s) inconnue(s)" in dashboard


def test_known_disabled_source_takes_precedence_over_unknown_connection():
    from cifereca.gui.sync_source_controls_overview import sync_axes_dashboard_summary
    src = source(enabled=False, connected=None)
    b = broker(
        schedule=dict(mode="periodic", interval_minutes=60),
        authorization=consent(enabled=True),
    )
    view = sync_source_controls_overview(src, b)
    assert "périodique (suspendue)" in view
    assert "autorisation activée (suspendue)" in view
    assert "source désactivée" in view
    assert "connexion de la source non confirmée" in view
    dashboard = sync_axes_dashboard_summary([src], b)
    assert "1 programmation(s) suspendue(s)" in dashboard
    assert "1 autorisation(s) suspendue(s)" in dashboard


@pytest.mark.parametrize("bad_source_id", [
    None, "", 42, True, object(),
    "opaque-" + "x" * 251,
    "https://private.invalid/?token=" + "SECRET" * 50,
])
def test_bad_source_scope_never_reads_another_accounts_policies(bad_source_id):
    """The card and aggregate must use the *same* bounded scope admission."""
    from cifereca.gui.sync_source_controls_overview import sync_axes_dashboard_summary
    calls = []
    src = source(source_id=bad_source_id)
    b = broker(
        schedule=dict(mode="periodic", interval_minutes=60),
        authorization=consent(enabled=True),
        calls=calls,
    )
    detail = sync_source_controls_overview(src, b)
    aggregate = sync_axes_dashboard_summary([src], b)
    assert detail == "Réglages · portée du compte indisponible"
    assert "0 programmation(s) active(s)" in aggregate
    assert "0 autorisation(s) effective(s)" in aggregate
    assert "1 programmation(s) inconnue(s)" in aggregate
    assert "1 autorisation(s) inconnue(s)" in aggregate
    assert calls == []
    assert "SECRET" not in detail + aggregate
    assert "private.invalid" not in detail + aggregate


def test_maximum_length_opaque_source_id_is_still_admitted():
    calls = []
    sid = "s" * 256
    src = source(source_id=sid)
    b = broker(
        schedule=dict(mode="periodic", interval_minutes=60),
        authorization=consent(enabled=True), calls=calls,
    )
    from cifereca.gui.sync_source_controls_overview import sync_axes_dashboard_summary
    detail = sync_source_controls_overview(src, b)
    aggregate = sync_axes_dashboard_summary([src], b)
    assert "Notices et droits : périodique" in detail
    assert "Fichiers : autorisation activée" in detail
    assert "1 programmation(s) active(s)" in aggregate
    assert "1 autorisation(s) effective(s)" in aggregate
    assert calls == [
        ("read-schedule", sid), ("read-consent", sid),
        ("read-schedule", sid), ("read-consent", sid),
    ]
    assert sid not in detail + aggregate
