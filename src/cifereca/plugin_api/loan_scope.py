"""Exact observed borrower matching; no permission, I/O or scope rebinding.

Reuses ProviderDescriptor.existing_primary_loan_account_alias. The declaration
belongs to the installed plugin, never to Loan.raw or a catalogue notice.
"""
from __future__ import annotations

from re import fullmatch

from cifereca.models import Account, Loan
from cifereca.providers.base import ProviderDescriptor


def loan_matches_account(
    loan: Loan, account: Account, descriptor: ProviderDescriptor,
) -> bool:
    """Match one observed loan's exact identity, connection and borrower.

    An alias is accepted only for the explicit empty-ID primary account of
    this descriptor's provider. None requires literal borrower equality.
    Missing/malformed descriptors fail closed. Even a match grants no action:
    caller must retain live registry/epoch, state, consent and claim checks.
    """
    if (type(loan) is not Loan or type(account) is not Account
            or type(descriptor) is not ProviderDescriptor):
        return False
    source = (loan.identity_id, loan.source_provider, loan.source_credential)
    owner = (account.identity_id, account.provider_id, account.credential_id)
    fields = (*source, *owner, account.unique_id, loan.source_account_id)
    if (any(type(value) is not str or len(value) > 1024 or chr(0) in value
            for value in fields)
            or not all(owner) or source != owner
            or type(descriptor.id) is not str or descriptor.id != account.provider_id):
        return False
    alias = descriptor.existing_primary_loan_account_alias
    if (alias is not None and (
            type(alias) is not str
            or fullmatch(r"[a-z][a-z0-9_-]{0,63}", alias) is None)):
        return False
    return (
        loan.source_account_id == account.unique_id
        or (alias is not None and account.is_primary is True
            and account.unique_id == "" and loan.source_account_id == alias)
    )


__all__ = ["loan_matches_account"]
