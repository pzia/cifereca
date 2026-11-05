"""Read-only, exact-borrower loan-cache observations for Settings (#4065).

The local preference is NOT a provider quota. Neither a cached positive loan
nor a missing row proves inventory completeness, free slots, or authorization.
This module does not contact providers, the database, or the synchronization
engine. It never renders personal IDs, book titles, or private diagnostics.
"""
from __future__ import annotations

from cifereca.models import Account, Loan
from cifereca.plugin_api.loan_inventory import LoanInventoryScope, _loan_matches_scope

_MAX_ACCOUNTS = 32
_MAX_LOANS = 10_000
_UNKNOWN = (
    "Inventaire exhaustif : non attesté par ce cache. "
    "Quota fournisseur : inconnu. Aucun emprunt restant calculé."
)


def _safe_scope(value: object, *, allow_empty: bool = False) -> bool:
    if type(value) is not str or len(value) > 128:
        return False
    if not value:
        return allow_empty
    return value == value.strip() and value.isprintable()


def _count_label(medium: str, count: int) -> str:
    name = "Numériques" if medium == "digital" else "Physiques"
    if count == 0:
        # Zero cached rows is never proof of a zero balance or no live loans.
        return f"{name} : non vérifiés"
    return f"{name} : {count} prêt(s) observé(s)"


def loan_cache_observations(
    *, provider_id: str, credential_id: str, identity_id: str,
    observed_borrowers: object, cached_loans: object,
) -> tuple[tuple[str, str], ...]:
    """Presentation-only borrower/medium rows from already loaded Core data.

    Every positive row must match provider, credential, selected identity and
    exact upstream borrower. Empty Paris primary ID is legitimate, but not a
    wildcard. Unknown/failed coverage cannot become an inferred quota.
    """
    unavailable = (("Prêts observés", "Observation locale indisponible. " + _UNKNOWN),)
    if not all(_safe_scope(value) for value in (
            provider_id, credential_id, identity_id)):
        return unavailable
    if (type(observed_borrowers) not in (tuple, list)
            or len(observed_borrowers) > 256
            or type(cached_loans) not in (tuple, list)
            or len(cached_loans) > _MAX_LOANS):
        return unavailable

    accounts: dict[str, Account] = {}
    for account in observed_borrowers:
        if type(account) is not Account:
            continue
        if (account.provider_id != provider_id
                or account.credential_id != credential_id
                or account.identity_id != identity_id
                or type(account.is_primary) is not bool
                or not _safe_scope(account.unique_id, allow_empty=True)):
            continue
        if account.unique_id in accounts:
            # An ambiguous/rebound borrower is not a safe display scope.
            return unavailable
        accounts[account.unique_id] = account
    if len(accounts) > _MAX_ACCOUNTS:
        return unavailable
    if not accounts:
        return ((
            "Prêts observés",
            "Aucun compte emprunteur confirmé dans le cache de cette connexion. " + _UNKNOWN,
        ),)

    evidence: dict[str, dict[str, set[str]]] = {
        borrower: {"digital": set(), "physical": set(), "unknown": set()}
        for borrower in accounts
    }
    scopes = {
        borrower: LoanInventoryScope.for_account(account)
        for borrower, account in accounts.items()
    }
    for loan in cached_loans:
        if type(loan) is not Loan or not _safe_scope(loan.id):
            continue
        # Reuse Core's exact typed-inventory predicate, including Paris'
        # narrow historical primary alias ('primary' for unique_id='').
        # Never guess a provider-specific alias in CoreUI. If a real linked
        # reader has that same ID, reject this ambiguous positive observation
        # rather than attributing it to *both* borrower rows.
        matches = [
            borrower for borrower, scope in scopes.items()
            if _loan_matches_scope(loan, scope)
        ]
        if len(matches) == 1:
            evidence[matches[0]][loan.medium].add(loan.id)

    ordered = sorted(accounts.values(), key=lambda a: (
        a.is_primary is not True, a.unique_id,
    ))
    rows: list[tuple[str, str]] = []
    linked_number = 0
    for account in ordered:
        if account.is_primary is True:
            label = "Prêts observés · compte principal"
        else:
            linked_number += 1
            label = f"Prêts observés · compte lié {linked_number}"
        found = evidence[account.unique_id]
        summaries = [
            _count_label("digital", len(found["digital"])),
            _count_label("physical", len(found["physical"])),
        ]
        if found["unknown"]:
            summaries.append(f"Support inconnu : {len(found['unknown'])} prêt(s) observé(s)")
        rows.append((label, " · ".join(summaries) + ". " + _UNKNOWN))
    return tuple(rows)
