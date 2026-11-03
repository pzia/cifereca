"""P1 #3416: a source explicitly switched OFF has historical, not active, errors.

No broker, provider session or GTK toolkit needed: the source projection is
a read-only CoreUI concern. Active sources must keep their real failure alerts.
"""
from types import SimpleNamespace

from cifereca.gui.sync_source_card_guidance import sync_source_card_guidance
from cifereca.gui.sync_source_controls_overview import (
    sync_schedule_status_subtitle, sync_file_consent_subtitle,
)
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


def test_scheduled_inventory_off_is_described_as_suspended_not_running():
    active = sync_schedule_status_subtitle("periodic", 60, available=True)
    suspended = sync_schedule_status_subtitle("periodic", 60, available=False)
    assert "Périodique" in active and "suspendue" not in active
    assert "Programmation suspendue" in suspended
    assert "Mode enregistré : Périodique" in suspended
    assert "Aucune actualisation automatique attendue" in suspended
    assert "N'autorise aucun téléchargement" in suspended
    assert "Au démarrage" in sync_schedule_status_subtitle(
        "on_start", 30, available=False
    )
    assert "suspendue" not in sync_schedule_status_subtitle(
        "manual", 30, available=False
    ).lower()


def test_suspended_file_consent_keeps_on_preference_without_effective_delivery():
    on = sync_file_consent_subtitle(enabled=True, available=True)
    suspended = sync_file_consent_subtitle(enabled=True, available=False)
    off = sync_file_consent_subtitle(enabled=False, available=False)
    assert "Activé :" in on
    assert "Autorisation enregistrée mais suspendue" in suspended
    assert "Aucun nouveau téléchargement automatique attendu" in suspended
    assert "Désactivé :" in off
    assert "Les fichiers déjà reçus sont conservés." in suspended
    assert "suspendue" not in off


def test_invalid_schedule_projection_never_reflects_supplier_payload():
    for mode, interval, available in (
        ("https://private.test/?token=x", 30, True),
        ("periodic", True, True),
        ("on_start", 0, True),
        ("on_start", 60, None),
    ):
        assert sync_schedule_status_subtitle(mode, interval, available=available) == (
            "Planification des notices indisponible."
        )


def test_unknown_activation_or_connection_never_receives_false_repair_advice():
    # Errors remain visible, but an untyped/missing signal is not proof the
    # user disabled the source or that a remote session is disconnected.
    for unknown in (None, "false", 0, 1, {}, []):
        activation = _source(enabled=unknown)
        alert = sync_source_attention(activation)
        assert alert is not None and alert[0] == "error"
        guidance = sync_source_card_guidance(activation, alert)
        assert guidance == "À faire : vérifier l’activation de cette source."
        assert "activer la source si souhaité" not in guidance

        connection = _source(enabled=True)
        connection.connected = unknown
        alert = sync_source_attention(connection)
        assert alert is not None and alert[0] == "error"
        guidance = sync_source_card_guidance(connection, alert)
        assert guidance == "À faire : vérifier la connexion de cette source."
        shown = sync_source_presentation(connection)
        assert shown.connection_label == "Non confirmée"
        assert shown.action_enabled is False
    disconnected = _source(enabled=True)
    disconnected.connected = False
    assert sync_source_card_guidance(
        disconnected, sync_source_attention(disconnected),
    ) == "À faire : reconnecter ce compte."
