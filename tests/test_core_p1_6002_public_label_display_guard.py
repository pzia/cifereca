"""P1 #6002 — untrusted presentation DTOs cannot inject private labels.

The same formatter is used by native Home and the external Home plugin.
No provider call or authorization state is involved.
"""
from __future__ import annotations

import pytest

from cifereca.plugin_api import (
    DigitalLoanAvailability, digital_loan_availability_lines,
)


def record(**kw):
    fields = dict(
        identity_id="alice", provider_id="demo", credential_id="PRIVATE-KEY",
        borrower_id="reader-secret", provider_label="Bibliothèque publique",
        connection_label="Connexion 1", borrower_label="Compte principal",
        observed_loans=2, personal_ceiling=4,
    )
    fields.update(kw)
    return DigitalLoanAvailability(**fields)


@pytest.mark.parametrize("attribute,bad", [
    ("provider_label", "https://private.example/?token=SECRETS"),
    ("connection_label", "Authorization: Bearer SECRET"),
    ("borrower_label", "password=PRIVATE"),
    ("provider_label", "Compte\nSECRET"),
    ("connection_label", " " + "A" * 160),
    ("borrower_label", object()),
    ("provider_label", True),
])
def test_public_dto_refuses_untrusted_labels(attribute, bad):
    with pytest.raises(ValueError, match="invalid_public_label"):
        record(**{attribute: bad})


@pytest.mark.parametrize("attribute,bad", [
    ("provider_label", "Bearer PRIVATE"),
    ("connection_label", "https://example.test/?token=SECRETS"),
    ("borrower_label", "newline\nPRIVATE"),
    ("observed_loans", object()),
    ("personal_ceiling", True),
    ("reason", object()),
    ("eligibility", "allowed"),
])
def test_renderer_fails_closed_even_when_frozen_dto_is_tampered(attribute, bad):
    item = record()
    object.__setattr__(item, attribute, bad)
    formatted = digital_loan_availability_lines((item,), identity_id="alice")
    assert formatted == ("Reste inconnu — projection des comptes invalide.",)
    assert "PRIVATE" not in str(formatted)
    assert "SECRETS" not in str(formatted)


def test_unicode_display_label_remains_readable_and_counts_unknown():
    item = record(provider_label="Médiathèque · Côte d'Azur",
                  connection_label="Connexion 1 · Ma carte",
                  borrower_label="Compte lié · Émile")
    lines = digital_loan_availability_lines((item,), identity_id="alice")
    assert "Médiathèque · Côte d'Azur" in lines[0]
    assert "2 prêt(s) numériques actifs observés" in lines[0]
    assert "plafond personnel 4 (indicatif)" in lines[0]
    assert "Reste inconnu" in lines[0]
    assert lines[-1].startswith("Total non calculable")
    assert "PRIVATE-KEY" not in str(lines)
    assert "reader-secret" not in str(lines)
