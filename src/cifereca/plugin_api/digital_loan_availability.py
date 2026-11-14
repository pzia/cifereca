"""P1 #6002 — passive, borrower-scoped *display* of digital loan availability.

The current Core stores positive loan statistics but no durable typed,
complete-and-fresh borrower/branch inventory, nor an attested remaining quota.
Consequently these records MUST remain unknown until a separate evidence
producer and durable freshness/revocation contract are actually delivered.
Nothing in this API grants a loan, changes permissions or reads a provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DigitalAvailabilityStatus = Literal["unknown", "estimated", "attested"]
DigitalAvailabilityReason = Literal[
    "inventory_unverified", "borrower_unverified", "account_disconnected",
    "provider_unverified", "account_disabled", "scope_ambiguous",
    "observation_stale", "subscription_unverified",
]


_PRIVATE_LABEL_MARKERS = (
    "http://", "https://", "token=", "password=", "secret=",
    "cookie=", "bearer ", "authorization:",
)


def _public_label(value: object) -> bool:
    # The UI must never interpolate a provider-supplied object or a credential
    # URL, including when a typed DTO is forged or mutated after construction.
    return (
        type(value) is str and len(value) <= 160
        and value == value.strip() and value.isprintable()
        and not any(marker in value.casefold()
                    for marker in _PRIVATE_LABEL_MARKERS)
    )


@dataclass(frozen=True, slots=True)
class DigitalLoanAvailability:
    """One exact identity/provider/credential/upstream borrower/digital branch.

    Internal scope coordinates are not user-facing text. `observed_loans`
    is an informational count of distinct positive, previously loaded active
    digital loans; it is *not* a complete inventory or a quota subtraction.
    Null means "not observed", NEVER zero.
    """
    identity_id: str
    provider_id: str
    credential_id: str = field(repr=False)
    borrower_id: str | None = field(default=None, repr=False)
    provider_label: str = ""
    connection_label: str = ""
    borrower_label: str = ""
    branch: str = "digital"
    observed_loans: int | None = None
    personal_ceiling: int | None = None
    remaining: int | None = None
    status: DigitalAvailabilityStatus = "unknown"
    reason: DigitalAvailabilityReason = "inventory_unverified"
    observed_at: str | None = None
    eligibility: Literal["not_verified"] = "not_verified"

    def __post_init__(self):
        # This first version is intentionally read-only unknown: the producer
        # cannot persist a proof of full, current inventory. Do not promote a
        # local ceiling or a cache observation into an estimated/attested N.
        if (self.status != "unknown" or self.remaining is not None
                or self.observed_at is not None
                or self.eligibility != "not_verified"
                or self.branch != "digital"):
            raise ValueError("digital_loan_availability_evidence_unavailable")
        if (type(self.identity_id) is not str or not self.identity_id
                or type(self.provider_id) is not str or not self.provider_id
                or type(self.credential_id) is not str or not self.credential_id
                or (self.borrower_id is not None and type(self.borrower_id) is not str)
                or self.reason not in DigitalAvailabilityReason.__args__):
            raise ValueError("digital_loan_availability_invalid_scope")
        if not all(_public_label(label) for label in (
                self.provider_label, self.connection_label, self.borrower_label,
        )):
            raise ValueError("digital_loan_availability_invalid_public_label")
        for amount in (self.observed_loans, self.personal_ceiling):
            if amount is not None and (type(amount) is not int or not 0 <= amount <= 10000):
                raise ValueError("digital_loan_availability_invalid_count")
        if self.personal_ceiling is not None and self.personal_ceiling > 99:
            raise ValueError("digital_loan_availability_invalid_ceiling")


_UNKNOWN_REASONS = {
    "inventory_unverified": "inventaire numérique complet et frais non attesté",
    "borrower_unverified": "emprunteur exact non confirmé",
    "account_disconnected": "connexion fournisseur non active",
    "account_disabled": "connexion désactivée",
    "provider_unverified": "fournisseur non qualifié",
    "scope_ambiguous": "portée du compte ambiguë",
    "observation_stale": "observation périmée",
    "subscription_unverified": "abonnement non attesté",
}


def digital_loan_availability_lines(
    rows: object, *, identity_id: str,
) -> tuple[str, ...]:
    """Shared public presentation for plugin Home and native fallback."""
    if type(rows) not in (tuple, list) or len(rows) > 128:
        return ("Reste inconnu — projection des comptes indisponible.",)
    if not rows:
        return ("Reste inconnu — aucun compte emprunteur vérifié.",)
    lines = []
    for record in rows:
        if (type(record) is not DigitalLoanAvailability
                or record.identity_id != identity_id
                or record.status != "unknown"
                or record.remaining is not None
                or record.observed_at is not None
                or record.branch != "digital"
                or record.eligibility != "not_verified"
                or type(record.reason) is not str
                or record.reason not in DigitalAvailabilityReason.__args__
                or not all(_public_label(label) for label in (
                    record.provider_label, record.connection_label,
                    record.borrower_label,
                ))
                or (record.observed_loans is not None and (
                    type(record.observed_loans) is not int
                    or not 0 <= record.observed_loans <= 10000))
                or (record.personal_ceiling is not None and (
                    type(record.personal_ceiling) is not int
                    or not 0 <= record.personal_ceiling <= 99))):
            return ("Reste inconnu — projection des comptes invalide.",)
        name = (
            record.provider_label + " · " + record.connection_label
            + " · " + record.borrower_label
        )
        facts = []
        if record.observed_loans is not None:
            facts.append(f"{record.observed_loans} prêt(s) numériques actifs observés")
        else:
            facts.append("prêts numériques actifs non vérifiés")
        if record.personal_ceiling is not None:
            facts.append(f"plafond personnel {record.personal_ceiling} (indicatif)")
        facts.append(_UNKNOWN_REASONS.get(record.reason, "preuve absente"))
        lines.append(name + " · Reste inconnu — " + " · ".join(facts))
    lines.append("Total non calculable · estimation ≠ autorisation d’emprunter.")
    return tuple(lines)


__all__ = [
    "DigitalLoanAvailability", "DigitalAvailabilityStatus",
    "DigitalAvailabilityReason", "digital_loan_availability_lines",
]
