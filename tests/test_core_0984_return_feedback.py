"""P0 #984: only verified provider inventory may produce a success notification."""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.gui.return_feedback import loan_return_feedback


@pytest.mark.parametrize(
    ("value", "channel", "expected", "text"),
    [
        (
            {
                "status": "paris_return_confirmed",
                "adobe_return_confirmed": True,
                "provider_return_confirmed": True,
                "paris_verification": "provider_absent",
            },
            "adept", "confirmed", "absent du compte Paris",
        ),
        (
            {
                "status": "partial", "adobe_return_confirmed": True,
                "paris_verification": "still_present",
            },
            "adept", "attention", "reste actif à Paris",
        ),
        (
            {
                "status": "partial", "adobe_return_confirmed": True,
                "paris_verification": "unavailable",
            },
            "adept", "attention", "n’a pas pu être confirmée",
        ),
        (
            {"status": "partial", "adobe_return_confirmed": False},
            "adept", "attention", "ambiguë",
        ),
        (
            {"status": "provider_action_available"},
            "adept", "attention", "Restituer à Paris (PNB)",
        ),
        (
            {"status": "failed", "code": "adept_return_verification_required"},
            "adept", "attention", "rejouée",
        ),
        (
            {"status": "adobe_return_requested"},
            "adept", "attention", "pas confirmée",
        ),
        (
            {"status": "adept_borrower_binding_required"},
            "adept", "attention", "ambiguë",
        ),
        (
            {"status": "paris_return_confirmed", "adobe_return_confirmed": True,
             "paris_verification": "provider_absent"},
            "adept", "attention", "n’a pas pu être confirmée",
        ),
        (
            {"status": "provider_return_confirmed", "provider_inventory_verified": True},
            "pnb", "confirmed", "absent",
        ),
        (
            {"status": "provider_return_confirmed", "provider_inventory_verified": False},
            "pnb", "attention", "non confirmée",
        ),
        (
            {"status": "pending_verification", "request_accepted": True},
            "pnb", "attention", "non confirmé",
        ),
        (
            {"status": "verification_unavailable", "request_accepted": True},
            "pnb", "attention", "non confirmé",
        ),
        (
            {"status": "still_active"},
            "pnb", "attention", "reste actif",
        ),
        (
            {"scope": "adobe_first_paris", "status": "partial",
             "adobe_return_confirmed": True,
             "reason": "adobe_return_takes_precedence"},
            "pnb", "attention", "ADEPT est déjà engagée",
        ),
        (
            {"success": True},
            "pnb", "attention", "n’a pas été vérifiée",
        ),
        (
            {"status": "failed"},
            "pnb", "attention", "non confirmée",
        ),
        (
            {"status": "attention_required", "code": "loan_return_busy"},
            "adept", "attention", "déjà en cours",
        ),
        (
            {"status": "attention_required", "code": "loan_return_busy"},
            "pnb", "attention", "déjà en cours",
        ),
        (
            {"status": "attention_required", "code": "loan_return_identity_conflict"},
            "pnb", "attention", "plusieurs identités Paris",
        ),
        (
            None, "adept", "attention", "incomplet",
        ),
        (
            [], "pnb", "attention", "incomplet",
        ),
    ],
)
def test_no_false_confirmed_return(value, channel, expected, text):
    kind, message = loan_return_feedback(value, channel=channel)
    assert kind == expected
    assert text in message
    if kind == "confirmed":
        assert (
            (channel == "adept" and value.get("provider_return_confirmed") is True)
            or (channel == "pnb" and value.get("provider_inventory_verified") is True)
        )


def test_unknown_channel_cannot_accidentally_claim_success():
    with pytest.raises(ValueError, match="unknown return channel"):
        loan_return_feedback({"status": "provider_return_confirmed"}, channel="other")


def test_gui_return_actions_show_outcome_instead_of_generic_success_toast():
    gui = (
        Path(__file__).parents[1] / "src" / "cifereca" / "gui" / "loans_controller.py"
    ).read_text(encoding="utf-8")
    block = gui.split("def _return_loan_result(", 1)[1]
    assert "loan_return_feedback(result, channel=channel)" in block
    assert "self.show_attention(message" in block
    assert "self.toast(message, 6)" in block
    assert 'channel="adept"' in block and 'channel="pnb"' in block
    assert "success_toast=" not in block
    assert "return_loan_prefer_adept(loan)" in block
    assert "return_digital(loan, explicit_retry=True)" in block
