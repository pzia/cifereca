"""#3416: sticky Sync dashboard reflects effective independent policy axes.

Only synthetic Core broker projections. No provider I/O, mutation, or source ID
is printed in the UI. Provider catalogue search is not catalogue refresh.
"""
from types import SimpleNamespace as NS

from cifereca.gui.sync_source_controls_overview import sync_axes_dashboard_summary


CAPS = ("remote_inventory", "scheduled_refresh", "local_materialization")


def source(sid, *, enabled=True, connected=True, capabilities=CAPS):
    return NS(source_id=sid, sync_capable=True, enabled=enabled,
              connected=connected, sync_capabilities=capabilities,
              provider_id="arbitrary-new-provider", label="Fournisseur futur")


def consent(*, enabled=False, configured=True, supported=True, valid=True):
    return dict(supported=supported, configured=configured, enabled=enabled,
                revision="revision-1" if configured else None, valid=valid)


def test_two_axes_count_only_effective_settings_and_preserve_suspended_choices():
    reads = []
    schedules = {
        "a": dict(mode="periodic", interval_minutes=60),
        "b": dict(mode="manual", interval_minutes=60),
        "c": dict(mode="on_start", interval_minutes=60),
        "d": dict(mode="periodic", interval_minutes=30),
    }
    authorizations = {
        "a": consent(enabled=True),
        "b": consent(configured=False),
        "c": consent(enabled=True),
        "d": consent(enabled=False),
    }
    broker = NS(
        inventory_refresh_schedule=lambda sid: (
            reads.append(("schedule", sid)) or schedules[sid]
        ),
        source_sync_consent=lambda sid: (
            reads.append(("consent", sid)) or authorizations[sid]
        ),
    )
    result = sync_axes_dashboard_summary([
        source("a"), source("b"), source("c", connected=False),
        source("d", enabled=False),
        source("search-only", capabilities=("search",)),
        NS(source_id="not-a-sync", sync_capable=False,
           sync_capabilities=CAPS),
    ], broker)
    assert "Notices et droits : 1 programmation(s) active(s)" in result
    assert "1 en manuel" in result
    assert "Réception de fichiers : 1 autorisation(s) effective(s)" in result
    assert "2 programmation(s) suspendue(s)" in result
    assert "1 autorisation(s) suspendue(s)" in result
    assert "1 choix de fichiers non défini(s)" in result
    assert reads == [
        ("schedule", "a"), ("consent", "a"),
        ("schedule", "b"), ("consent", "b"),
        ("schedule", "c"), ("consent", "c"),
        ("schedule", "d"), ("consent", "d"),
    ]
    assert "arbitrary-new-provider" not in result
    assert "Fournisseur futur" not in result


def test_missing_or_malformed_read_is_unknown_not_disabled_or_an_authorization():
    def fail(_sid):
        raise RuntimeError("https://private.invalid?token=PRIVATE")
    broker = NS(inventory_refresh_schedule=fail,
                source_sync_consent=lambda sid: {
                    **consent(enabled=True), "private": "PRIVATE",
                })
    result = sync_axes_dashboard_summary([source("a")], broker)
    assert "0 programmation(s) active(s)" in result
    assert "0 autorisation(s) effective(s)" in result
    assert "1 programmation(s) inconnue(s)" in result
    assert "1 autorisation(s) inconnue(s)" in result
    assert "PRIVATE" not in result
    assert "token=" not in result


def test_old_active_consent_without_new_activation_capability_is_suspended():
    broker = NS(
        source_sync_consent=lambda _sid: consent(enabled=True, supported=False),
    )
    result = sync_axes_dashboard_summary(
        [source("legacy", capabilities=("local_materialization",))], broker,
    )
    assert "0 autorisation(s) effective(s)" in result
    assert "1 autorisation(s) suspendue(s)" in result


def test_bounded_unknown_source_scope_never_triggers_credential_lookup():
    called = []
    broker = NS(
        source_sync_consent=lambda sid: called.append(sid),
        inventory_refresh_schedule=lambda sid: called.append(sid),
    )
    result = sync_axes_dashboard_summary([source("x" * 257)], broker)
    assert "1 programmation(s) inconnue(s)" in result
    assert "1 autorisation(s) inconnue(s)" in result
    assert called == []
    assert sync_axes_dashboard_summary([source("a")] * 1025, broker) == (
        "Réglages par flux indisponibles"
    )


def test_gtk_sticky_header_shows_axes_without_triggering_any_sync():
    from tests.test_core_ui_1347_sync_action_scope import _fixture, _source
    window, broker, calls, _build, _page = _fixture(sources=[
        _source(provider_id="unknown-provider",
                sync_capabilities=("remote_inventory", "scheduled_refresh",
                                   "local_materialization")),
    ])
    assert "1 source(s) · 1 synchronisable(s)" in window._sync_summary.label
    assert "Notices et droits : 0 programmation(s) active(s)" in window._sync_summary.label
    assert "Réception de fichiers : 0 autorisation(s) effective(s)" in window._sync_summary.label
    assert "1 autorisation(s) inconnue(s)" in window._sync_summary.label
    assert "1 programmation(s) inconnue(s)" in window._sync_summary.label
    assert calls == [("sources", "alice")]
    assert not window.runner.tasks
