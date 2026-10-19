"""Resolve source-bound borrower accounts without performing loan mutations.

The legacy cache lookup and the strict return preflight are distinct contracts.
Return preflight rejects missing borrower provenance *before* invoking lookup,
then requires the exact connected provider/credential session. The service
retains all locks, provider requests, verification and repeated-return guards.
"""


def account_for_loan(loan, *, accounts, current_account_get, configuration_error):
    """Resolve the exact recorded borrower, preserving legacy-cache fallback."""
    candidates = [a for a in accounts if a.provider_id == loan.source_provider and a.credential_id == loan.source_credential]
    if loan.source_account_id is not None:
        # Paris stores a stable sentinel while the real primary Account has an empty UID.
        if loan.source_account_id == 'primary':
            primary = next((a for a in candidates if a.is_primary), None)
            if primary is not None:
                return primary
        for account in candidates:
            if account.unique_id == loan.source_account_id:
                return account
        # The original lookup error interpolated borrower ID, credential,
        # provider and loan ID directly into a UI-facing exception. This
        # information may include signed URLs or personal account data.
        raise configuration_error('Compte emprunteur introuvable pour ce prêt')
    # Legacy cache compatibility only; strict return preflight rejects missing provenance.
    if current_account_get() in candidates:
        return current_account_get()
    primary = next((a for a in candidates if a.is_primary), None)
    if primary is not None:
        return primary
    if candidates:
        return candidates[0]
    raise configuration_error('Aucun compte connecté pour ce prêt')


def return_loan_identity(loan, *, resolve_account, session_key, lookup_session, attention_error):
    """Require *exact* Paris borrower provenance before mutating anything."""
    from .loan_return_guard import return_key
    if not loan.id or loan.source_account_id is None:
        raise attention_error('Le compte emprunteur de ce prêt est inconnu. Actualisez les emprunts.', code='loan_return_borrower_unproven')
    account = resolve_account(loan)
    expected = str(loan.source_account_id or 'primary')
    actual = str(account.unique_id or 'primary')
    if expected != actual or account.provider_id != loan.source_provider or account.credential_id != loan.source_credential:
        raise attention_error('Le compte emprunteur de ce prêt ne correspond pas à sa provenance.', code='loan_return_borrower_mismatch')
    key = session_key(loan.source_provider, loan.source_credential)
    session = lookup_session(key)
    if session is None or not session.connected:
        raise attention_error("La connexion exacte de l'emprunteur est indisponible.", code='loan_return_source_unavailable')
    return (account, session, return_key(loan.source_provider, loan.source_credential, expected, str(loan.id)))
