"""#1063: local ADEPT status presentation does not attest provider permission."""
from cifereca.gui.settings_editor_actions import _local_adept_binding_caption


def test_ready_is_explicitly_local_and_not_adobe_authorization():
    caption = _local_adept_binding_caption({"status": "ready"})
    assert caption.startswith("Prêt (liaisons locales)")
    assert "Autorisation Adobe non testée" in caption


def test_missing_and_ambiguous_connections_remain_distinct_and_fail_closed():
    missing = _local_adept_binding_caption({
        "status": "to_configure", "reason": "missing_paris_connection",
    })
    multiple = _local_adept_binding_caption({
        "status": "to_verify", "reason": "multiple_paris_connections",
    })
    assert "À configurer" in missing
    assert "À vérifier" in multiple and "plusieurs connexions" in multiple


def test_no_private_credential_or_profile_id_leaks_into_subtitle():
    state = {
        "status": "to_verify", "reason": "missing_primary_account",
        "credential_id": "secret-password-123", "adept_profile_id": "private-reader-id",
    }
    caption = _local_adept_binding_caption(state)
    assert "secret-password-123" not in caption
    assert "private-reader-id" not in caption
    assert "Aucun profil ADEPT choisi automatiquement" in caption


def test_unrecognized_reason_remains_fail_closed():
    caption = _local_adept_binding_caption({"status": "other", "reason": "future_state"})
    assert "À vérifier" in caption
    assert "Aucune autorisation" not in caption
