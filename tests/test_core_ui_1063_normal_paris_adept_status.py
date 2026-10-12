"""P0 #1063: accessible, non-authorizing presentation of normal bindings."""
from cifereca.gui.paris_adept_presentation import normal_paris_adept_summary


def test_ready_is_explicitly_local_and_not_adobe_authorization():
    text = normal_paris_adept_summary({"status": "ready"})
    assert text.startswith("Prêt (liaisons locales)")
    assert "Autorisation Adobe non testée" in text


def test_missing_and_multiple_connections_have_distinct_next_actions():
    missing = normal_paris_adept_summary({
        "status": "to_configure", "reason": "missing_paris_connection",
    })
    multiple = normal_paris_adept_summary({
        "status": "to_verify", "reason": "multiple_paris_connections",
    })
    assert "À configurer" in missing
    assert "À vérifier" in multiple and "plusieurs connexions" in multiple


def test_no_private_credential_or_profile_id_leaks_into_subtitle():
    status = {
        "status": "to_verify", "reason": "missing_primary_account",
        "credential_id": "secret-password-123", "adept_profile_id": "private-reader-id",
    }
    summary = normal_paris_adept_summary(status)
    assert "secret-password-123" not in summary
    assert "private-reader-id" not in summary
    assert "Aucun profil ADEPT choisi automatiquement" in summary


def test_unrecognized_reason_remains_fail_closed():
    text = normal_paris_adept_summary({"status": "other", "reason": "future_state"})
    assert "À vérifier" in text
