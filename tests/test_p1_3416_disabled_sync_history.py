"""P1 #3416: a source explicitly switched OFF has historical, not active, errors.

No broker, provider session or GTK toolkit needed: the source projection is
a read-only CoreUI concern. Active sources must keep their real failure alerts.
"""
from types import SimpleNamespace

from cifereca.gui.sync_source_card_guidance import sync_source_card_guidance
from cifereca.gui.sync_view import sync_source_attention, sync_source_presentation


def _source(*, enabled: object, status: str = "failed", phases=None):
    if phases is None:
        phases = (
            SimpleNamespace(
                phase="discover_rights", status="failed",
                last_attempt="2026-11-02T19:05:00Z", last_success=None,
            ),
            SimpleNamespace(
                phase="materialize", status="failed",
                last_attempt="2026-11-02T19:06:00Z", last_success=None,
            ),
        )
    return SimpleNamespace(
        source_id="synthetic:future-provider:account-a",
        identity_id="identity-a",
        provider_id="future-provider",
        label="Compte de test",
        enabled=enabled,
        connected=True,
        sync_capable=True,
        sync_capabilities=("remote_inventory", "local_materialization"),
        status=status,
        error="https://private-provider.test/?access_token=never-show",
        last_attempt="2026-11-02T19:06:00Z",
        last_success="2026-11-01T11:00:00Z",
        phase_states=phases,
    )


def test_explicit_off_hides_old_error_from_active_attention():
    source = _source(enabled=False)
    assert sync_source_attention(source) is None
    view = sync_source_presentation(source, canonical=True)
    assert view.sync_label == "Désactivée · aucune synchronisation attendue"
    assert not view.action_enabled
    assert "Synchronisation : Désactivée · aucune synchronisation attendue" in view.summary
    assert "Dernier essai avant désactivation :" in view.summary
    assert "Historique du dernier cycle avant désactivation :" in view.summary
    assert "Notices et droits : Échec" in view.summary
    assert "Dernier succès confirmé :" in view.summary
    assert "access_token=" not in view.summary
    assert sync_source_card_guidance(source, sync_source_attention(source)) == ""
    assert "Activez cette source" not in view.disabled_reason


def test_switching_on_again_restores_active_alerts_without_historical_label():
    source = _source(enabled=True)
    assert sync_source_attention(source) == (
        "error", "Téléchargement local : échec — action nécessaire"
    )
    view = sync_source_presentation(source, canonical=True)
    assert view.sync_label == "Échec"
    assert view.action_enabled
    assert "Historique du dernier cycle avant désactivation" not in view.summary
    assert "Dernier essai avant désactivation" not in view.summary
    assert sync_source_card_guidance(source, sync_source_attention(source)) == (
        "À faire : examiner le dernier cycle au survol."
    )


def test_unknown_enabled_does_not_silence_a_real_failure():
    # The suppression is narrowly scoped to an explicit Core enabled=False.
    source = _source(enabled=None)
    assert sync_source_attention(source)[0] == "error"


def test_disabled_partial_and_empty_history_remain_neutral():
    for phases in ((), (
        SimpleNamespace(
            phase="discover_rights", status="partial",
            last_attempt=None, last_success=None,
        ),
    )):
        source = _source(enabled=False, status="partial", phases=phases)
        source.error = "Synchronisation partielle"
        assert sync_source_attention(source) is None
        view = sync_source_presentation(source)
        assert view.sync_label == "Désactivée · aucune synchronisation attendue"
        assert "Synchronisation : Partielle" not in view.summary
        if phases:
            assert "Historique du dernier cycle avant désactivation :" in view.summary
        else:
            assert "Historique du dernier cycle avant désactivation :" not in view.summary


def test_disabled_source_never_reports_disconnected_account_as_active_incident():
    source = _source(enabled=False)
    source.connected = False
    assert sync_source_attention(source) is None
    assert "Désactivée" in sync_source_presentation(source).sync_label
