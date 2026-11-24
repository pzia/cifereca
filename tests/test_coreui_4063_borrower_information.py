from cifereca.gui.borrower_information_action import advisory_text, _candidate


def test_advisory_copy_does_not_promote_permission_or_echo_secret():
    text = advisory_text({"observed_at": 1700000000, "item_offer": "unavailable",
                          "permission": "yes", "remaining_quota": 42,
                          "secret": "private-url"})
    assert "Indisponibilité observée" in text
    assert "Permission : inconnue" in text
    assert "Quota : inconnu" in text
    assert "private-url" not in text


def test_invalid_notice_and_absent_observation_are_safe():
    assert _candidate(object(), object()) is None
    assert advisory_text(None) == "Informations non vérifiées."
