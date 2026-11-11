"""Provider-neutral shape check for an already-authenticated private batch.

This deliberately does not authenticate a provider or prove ownership.
The plugin acquisition boundary must first attest the exact credential and
private inventory source; a public catalogue cannot turn owned=True into
a right by calling this helper.
"""
from __future__ import annotations

from inspect import getattr_static

from ..models import SearchResult

MAX_PRIVATE_ROWS = 100_000


def private_inventory_rows_match_scope(
    rows: object, *, provider_id: str, credential_id: str,
    collection: str, scenario: str, scoped_record_valid,
) -> bool:
    """Reject a whole batch if one row contradicts its attested source scope."""
    if (type(provider_id) is not str or not provider_id
            or type(credential_id) is not str or not credential_id
            or type(collection) is not str or not collection
            or type(scenario) is not str or not scenario
            or not callable(scoped_record_valid)
            or type(rows) is not list or len(rows) > MAX_PRIVATE_ROWS):
        return False
    try:
        return all(
            isinstance(row, SearchResult)
            and row.source_provider == provider_id
            and row.source_credential == credential_id
            and row.source_collection == collection
            and row.scenario == scenario
            and row.owned is True
            and row.id == row.record_id
            and scoped_record_valid(row.record_id) is True
            for row in rows
        )
    except Exception:
        return False



def credential_scoped_private_inventory_rows_valid(
    rows: object, *, provider_id: str, credential_id: str,
    collection: str, scenario: str,
) -> bool:
    """Structural provenance check; no provider-specific rights or I/O.

    A provider account/session must already have been independently bound
    before this purely descriptive, atomic batch check is used.
    """
    from .credential_scoped_record import exact_credential_scoped_record
    return private_inventory_rows_match_scope(
        rows, provider_id=provider_id, credential_id=credential_id,
        collection=collection, scenario=scenario,
        scoped_record_valid=lambda item: (
            exact_credential_scoped_record(
                item, provider_id=provider_id, credential_id=credential_id,
            ) is not None
        ),
    )


def declared_private_inventory_batch_valid(
    rows: object, *, session: object, provider_id: str, credential_id: str,
) -> bool:
    """Validate a provider-declared private-record shape without vendor logic.

    A selected-purchase producer must declare its shape explicitly. Missing,
    malformed or unknown declarations fail closed, rather than downgrading to
    a permissive legacy mirror. Providers without selected-purchase support
    retain their existing independent inventory contracts.

    This is structural metadata only: it never attests ownership or an offer.
    """
    try:
        branch = getattr_static(session, "selected_purchase_branch", None)
        contract = getattr_static(session, "private_inventory_row_contract", None)
        if branch is None and contract is None:
            return True
        if (type(contract) is not tuple or len(contract) != 3
                or type(branch) is not str or not branch
                or type(provider_id) is not str or not provider_id
                or type(credential_id) is not str or not credential_id):
            return False
        collection, scenario, record_schema = contract
        if (type(collection) is not str or not collection
                or type(scenario) is not str or not scenario
                or type(record_schema) is not str
                or record_schema != "credential_scoped_record_v1"):
            return False
        return credential_scoped_private_inventory_rows_valid(
            rows, provider_id=provider_id, credential_id=credential_id,
            collection=collection, scenario=scenario,
        )
    except Exception:
        return False


def selected_private_offer_hint(service: object, item: object) -> bool | None:
    """Passive, provider-neutral visibility hint for selected private offers.

    True only means that the UI may offer an explicit verification action;
    it never grants a GET, an offer, ownership or a durable right. None
    delegates non-selected legacy sources to their existing display policy.
    """
    if type(item) is not SearchResult:
        return None
    provider = item.source_provider
    credential = item.source_credential
    if (type(provider) is not str or not provider
            or type(credential) is not str or not credential):
        return False
    try:
        # The Core's already loaded session map is read without connecting to
        # a provider. An unbound private row is not an executable offer.
        session = service._sessions.get(service._session_key(provider, credential))
        branch = getattr_static(session, "selected_purchase_branch", None)
        contract = getattr_static(session, "private_inventory_row_contract", None)
    except Exception:
        session = None
        branch = contract = None

    if branch is None and contract is None:
        # Do not let an unbound, unsigned private library row fall through to
        # a generic direct_download=True supplier capability. A public offer
        # with a real delivery URL keeps its independent legacy policy.
        if item.owned is True and item.download_url is None:
            return False
        return None

    if (type(branch) is not str or not branch
            or type(contract) is not tuple or len(contract) != 3
            or type(contract[0]) is not str or not contract[0]
            or type(contract[1]) is not str or not contract[1]
            or type(contract[2]) is not str
            or contract[2] != "credential_scoped_record_v1"
            or item.downloadable is not True
            or item.download_url is not None
            or item.medium != "digital"
            or credential == "__public__"):
        return False

    # Preserve the source-enable check normally performed by
    # _session_for_source, but do not resolve or contact the provider.
    enabled = getattr(service, "_provider_enabled_for_identity", None)
    if callable(enabled):
        try:
            if enabled(provider) is not True:
                return False
        except Exception:
            return False

    collection, scenario, _schema = contract
    if not credential_scoped_private_inventory_rows_valid(
        [item], provider_id=provider, credential_id=credential,
        collection=collection, scenario=scenario,
    ):
        return False

    # A metadata row from another member's credential can never enable the
    # button on the selected profile. Require the one exact account.
    identity = getattr(getattr(service, "current_identity", None), "identity_id", None)
    if type(identity) is not str or not identity:
        return False
    accounts = [
        account for account in service.accounts
        if getattr(account, "provider_id", None) == provider
        and getattr(account, "credential_id", None) == credential
    ]
    if len(accounts) != 1 or accounts[0].identity_id != identity:
        return False
    return getattr(getattr(session, "capabilities", None), "direct_download", None) is True
