"""Legacy source-ID primary alias guard shared by providers.

The old sync source key uses provider:credential:primary when the upstream
account ID is empty. A literal upstream account with exactly that value
cannot inherit an ON bit from the primary borrower. This is a negative
admission guard, never a source/loan/purchase authorization.
"""
from __future__ import annotations


def ambiguous_legacy_source_account_id(
    provider_id: object, credential_id: object, account_id: object,
) -> bool:
    """Fail closed for invalid, whitespace-normalized or aliasing IDs.

    The empty account ID stays a distinct legacy PRIMARY fallback handled
    by each caller's existing independent primary/account binding checks.
    """
    if (type(provider_id) is not str or not provider_id
            or type(credential_id) is not str or not credential_id
            or type(account_id) is not str):
        return True
    return bool(account_id) and (
        account_id != account_id.strip()
        or account_id == provider_id + ":" + credential_id + ":primary"
    )
