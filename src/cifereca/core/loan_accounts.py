"""Resolve source-bound borrower accounts without performing loan mutations.

Historical caches without borrower provenance remain readable as data but
cannot select an arbitrary connected account for provider mutations.
Return preflight rejects missing borrower provenance *before* invoking lookup,
then requires the exact connected provider/credential session. The service
retains all locks, provider requests, verification and repeated-return guards.
"""

from inspect import getattr_static
from re import fullmatch
from types import SimpleNamespace


def account_for_loan(
    loan, *, accounts, configuration_error, primary_alias: str | None = None,
):
    """Resolve one exact borrower under an installed source alias convention.

    A provider-declared primary alias is descriptive only. It maps to an
    *empty-ID* primary account; it cannot override a real upstream reader
    with that literal ID. Duplicate matches are never first-row-wins.
    """
    borrower = getattr(loan, "source_account_id", None)
    provider = getattr(loan, "source_provider", None)
    credential = getattr(loan, "source_credential", None)
    if (type(borrower) is not str or type(provider) is not str or not provider
            or type(credential) is not str or not credential):
        raise configuration_error('Compte emprunteur non attesté pour ce prêt')
    # A malformed optional alias grants no special lookup. An exact upstream
    # account ID can still match, independently of this display convention.
    # Keep the same strict grammar as the canonical loan_matches_account
    # contract; a malformed plugin descriptor cannot invent a borrower alias.
    if (primary_alias is not None and (
            type(primary_alias) is not str
            or fullmatch(r"[a-z][a-z0-9_-]{0,63}", primary_alias) is None)):
        primary_alias = None
    matches = []
    for account in accounts:
        account_provider = getattr(account, "provider_id", None)
        account_credential = getattr(account, "credential_id", None)
        account_id = getattr(account, "unique_id", None)
        if (type(account_provider) is not str or account_provider != provider
                or type(account_credential) is not str or account_credential != credential
                or type(account_id) is not str):
            continue
        # An empty borrower ID is an exact primary-account convention,
        # never an authority to select an unlabeled linked account.
        exact = (
            account_id == borrower
            and (borrower != "" or getattr(account, "is_primary", None) is True)
        )
        aliased = (
            primary_alias is not None and borrower == primary_alias
            and account_id == "" and getattr(account, "is_primary", None) is True
        )
        if exact or aliased:
            matches.append(account)
    if len(matches) == 1:
        return matches[0]
    # Never leak a loan ID, credential, borrower ID or signed URL through
    # the public exception; duplicate and missing are equally unproven.
    raise configuration_error('Compte emprunteur introuvable pour ce prêt')


def return_loan_identity(loan, *, resolve_account, session_key, lookup_session, attention_error):
    """Require exact source-bound borrower provenance before mutating anything."""
    from .loan_return_guard import return_key
    # A corrupt or plugin-provided Loan must never be stringified to construct
    # a borrower/operation key or compared via hostile __eq__/__bool__. The
    # empty borrower UID remains the documented Paris primary sentinel.
    def snapshot():
        try:
            values = (
                getattr(loan, 'id', None),
                getattr(loan, 'source_provider', None),
                getattr(loan, 'source_credential', None),
                getattr(loan, 'source_account_id', None),
            )
            if (all(type(value) is str for value in values)
                    and all(values[:3])):
                return values
        except Exception:
            pass
        return None

    source = snapshot()
    if source is None:
        raise attention_error(
            'Le compte emprunteur de ce prêt est inconnu. Actualisez les emprunts.',
            code='loan_return_borrower_unproven',
        )
    loan_id, provider, credential, borrower = source

    def require_current():
        if snapshot() != source:
            raise attention_error(
                'La provenance du prêt a changé. Actualisez les emprunts.',
                code='loan_return_borrower_mismatch',
            )

    account = resolve_account(loan)
    require_current()
    unique_id = getattr(account, 'unique_id', None)
    provider_id = getattr(account, 'provider_id', None)
    credential_id = getattr(account, 'credential_id', None)
    expected = borrower or 'primary'
    require_current()
    if (type(unique_id) is not str
            or type(provider_id) is not str
            or type(credential_id) is not str
            or expected != (unique_id or 'primary')
            or provider_id != provider
            or credential_id != credential):
        raise attention_error(
            'Le compte emprunteur de ce prêt ne correspond pas à sa provenance.',
            code='loan_return_borrower_mismatch',
        )
    key = session_key(provider, credential)
    require_current()
    session = lookup_session(key)
    require_current()
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
        provider, credential, expected, loan_id,
    ))
