"""Resolve source-bound borrower accounts without performing loan mutations.

Historical caches without borrower provenance remain readable as data but
cannot select an arbitrary connected account for provider mutations.
Return preflight rejects missing borrower provenance *before* invoking lookup,
then requires the exact connected provider/credential session. The service
retains all locks, provider requests, verification and repeated-return guards.
"""

from inspect import getattr_static
from types import SimpleNamespace


def account_for_loan(loan, *, accounts, configuration_error):
    """Resolve an explicitly recorded borrower; never guess from navigation."""
    borrower = getattr(loan, 'source_account_id', None)
    # Historical missing provenance is descriptive only. In particular,
    # current_account/primary/first account must never supply a borrower to a
    # later provider GET, ACSM acquisition or return mutation.
    if type(borrower) is not str:
        raise configuration_error('Compte emprunteur non attesté pour ce prêt')
    candidates = [a for a in accounts if a.provider_id == loan.source_provider and a.credential_id == loan.source_credential]
    # Paris stores a stable sentinel while the real primary Account has an empty UID.
    if borrower == 'primary':
        primary = next((a for a in candidates if a.is_primary), None)
        if primary is not None:
            return primary
    for account in candidates:
        if account.unique_id == borrower:
            return account
    # The original lookup error interpolated borrower ID, credential,
    # provider and loan ID directly into a UI-facing exception. This
    # information may include signed URLs or personal account data.
    raise configuration_error('Compte emprunteur introuvable pour ce prêt')


def return_loan_identity(loan, *, resolve_account, session_key, lookup_session, attention_error):
    """Require *exact* Paris borrower provenance before mutating anything."""
    from .loan_return_guard import return_key
    # A corrupt or plugin-provided Loan must never be stringified to construct
    # a borrower/operation key or compared via hostile __eq__/__bool__. The
    # empty borrower UID remains the documented Paris primary sentinel.
    source = (
        getattr(loan, 'id', None),
        getattr(loan, 'source_provider', None),
        getattr(loan, 'source_credential', None),
        getattr(loan, 'source_account_id', None),
    )
    if (any(type(value) is not str for value in source)
            or not source[0] or not source[1] or not source[2]):
        raise attention_error(
            'Le compte emprunteur de ce prêt est inconnu. Actualisez les emprunts.',
            code='loan_return_borrower_unproven',
        )
    account = resolve_account(loan)
    unique_id = getattr(account, 'unique_id', None)
    provider_id = getattr(account, 'provider_id', None)
    credential_id = getattr(account, 'credential_id', None)
    expected = loan.source_account_id or 'primary'
    if (type(unique_id) is not str
            or type(provider_id) is not str
            or type(credential_id) is not str
            or expected != (unique_id or 'primary')
            or provider_id != loan.source_provider
            or credential_id != loan.source_credential):
        raise attention_error(
            'Le compte emprunteur de ce prêt ne correspond pas à sa provenance.',
            code='loan_return_borrower_mismatch',
        )
    key = session_key(loan.source_provider, loan.source_credential)
    session = lookup_session(key)
    # This is an admission preflight, not a provider status refresh. A
    # connected property or overridden __getattribute__ could issue HTTP
    # before the Core's later loan-action scope lease is even captured.
    # Only a real initialized scalar may establish session readiness.
    session_type = type(session)
    # The legacy tests/plugins also accept SimpleNamespace sessions. Its
    # built-in C __getattribute__ is inert, unlike a plugin override.
    safe_lookup = getattr_static(session_type, '__getattribute__', None)
    connected = (
        getattr_static(session, 'connected', None)
        if safe_lookup in (
            object.__getattribute__, SimpleNamespace.__getattribute__,
        ) else None
    )
    if connected is not True:
        raise attention_error(
            "La connexion exacte de l'emprunteur est indisponible.",
            code='loan_return_source_unavailable',
        )
    return (account, session, return_key(
        loan.source_provider, loan.source_credential, expected, loan.id,
    ))
