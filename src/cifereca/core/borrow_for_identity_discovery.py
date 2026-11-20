"""#4988: passive discovery of *possible* lenders for the ordinary borrow flow.

This module is not the dual-positive-evidence preview of #4662. It may show
a locally configured lender, but it never claims the supplier will accept the
loan or that copying to the requester is permitted. No provider call occurs.
The future explicit-click executor must rebind the exact source and claim.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from ..models import Account, SearchResult
from ..plugin_api.lending_capabilities import assess_lending_operation
from .borrow_action_scope import _fields, borrow_claim_key
from .borrow_for_identity_live import _lender_label
from .borrow_for_identity_preview import _display_account_label
from .loan_action_scope import _inert_session_accounts, _inert_session_state


@dataclass(frozen=True, slots=True)
class PossibleBorrowLender:
    """Inert local account candidate, never an upstream offer or permission."""

    label: str
    lender_identity_id: str = field(repr=False)
    _account: Account = field(repr=False, compare=False)
    _notice: SearchResult = field(repr=False, compare=False)
    supplier_eligibility: Literal["not_verified"] = "not_verified"
    executable: bool = False
    # A SearchResult and Account can be mutated in-place after rendering;
    # object identity alone is not an immutable edition/source snapshot.
    _captured_fields: tuple[object, ...] = field(
        init=False, repr=False, compare=False,
    )

    def __post_init__(self) -> None:
        # Future UI callers must never reinterpret a locally found account
        # as provider permission or an executable loan admission.
        account, notice = self._account, self._notice
        if (self.executable is not False
                or self.supplier_eligibility != "not_verified"
                or type(self.label) is not str
                or not 0 < len(self.label) <= 80
                or not self.label.isprintable()
                or type(account) is not Account
                or type(notice) is not SearchResult
                or self.lender_identity_id != account.identity_id
                or account.is_primary is not True
                or borrow_claim_key(account, notice) is None):
            raise ValueError("invalid_possible_borrow_lender")
        object.__setattr__(self, "_captured_fields", _fields(account, notice))



def possible_lender_matches(candidate, notice, *, requesting_identity_id):
    """Pure check of an already captured inert lender, never permission.

    A notice or account can change in place while a discovery task is queued,
    or while a chooser is open. An object reference alone is not a receipt.
    """
    if (type(candidate) is not PossibleBorrowLender
            or type(notice) is not SearchResult
            or candidate._notice is not notice
            or candidate.executable is not False
            or candidate.supplier_eligibility != "not_verified"
            or type(requesting_identity_id) is not str or not requesting_identity_id):
        return False
    account = candidate._account
    return (type(account) is Account
            and account.is_primary is True
            and type(candidate.lender_identity_id) is str
            and candidate.lender_identity_id != requesting_identity_id
            and candidate.lender_identity_id == account.identity_id
            and borrow_claim_key(account, notice) is not None
            and type(candidate._captured_fields) is tuple
            and candidate._captured_fields == _fields(account, notice))


def discover_possible_borrow_lenders(
    service: Any, *, requesting_identity_id: str,
    notices: tuple[SearchResult, ...],
) -> tuple[PossibleBorrowLender, ...]:
    """Enumerate 0/1/N *local* lender choices without provider IO or claims.

    Each notice is one exact provider/credential/branch/record observation.
    This deliberately does not join editions by title/ISBN or manufacture
    BorrowerOperationEvidence. It never switches the active identity.
    """
    if (type(requesting_identity_id) is not str
            or not requesting_identity_id
            or type(notices) is not tuple or len(notices) > 64
            or any(type(item) is not SearchResult for item in notices)):
        return ()
    try:
        # The caller supplies the requester. Resolve the exact registered
        # profile, never the navigation selector (which may be absent).
        requester = service.identity_by_id(requesting_identity_id)
        if requester is None or requester.identity_id != requesting_identity_id:
            return ()
        context_epoch = service._account_context_epoch
        source_epoch = service._source_context_epoch
        if (type(context_epoch) is not int or context_epoch < 0
                or type(source_epoch) is not int or source_epoch < 0):
            return ()
        accounts = service.accounts
        if type(accounts) not in (list, tuple) or len(accounts) > 512:
            return ()
    except Exception:
        return ()

    result: list[PossibleBorrowLender] = []
    seen: set[tuple[str, ...]] = set()
    for item in notices:
        try:
            if (item.medium != "digital"
                    or type(item.source_provider) is not str
                    or not item.source_provider
                    or type(item.source_credential) is not str
                    or not item.source_credential
                    or type(item.docbase) is not str or not item.docbase
                    or type(item.record_id) is not str or not item.record_id):
                continue
            provider, credential = item.source_provider, item.source_credential
            matching = [
                a for a in accounts
                if (type(a) is Account and a.is_primary is True
                    and a.identity_id != requesting_identity_id
                    and a.provider_id == provider
                    and a.credential_id == credential)
            ]
            if len(matching) != 1:
                continue
            account = matching[0]
            # The session key is provider+credential, not profile. If this
            # key is registered to another profile too, the apparent
            # lender session cannot be safely attributed to A.
            if any(
                type(other) is not Account
                or (other.provider_id == provider
                    and other.credential_id == credential
                    and other.identity_id != account.identity_id)
                for other in accounts
            ):
                continue
            route = (
                account.identity_id, provider, credential, account.unique_id,
                item.docbase, item.record_id, item.source_collection or "",
            )
            if route in seen or borrow_claim_key(account, item) is None:
                continue
            if service.identity_by_id(account.identity_id) is None:
                continue
            if service.storage.identity_for_connection(
                    provider, credential) != account.identity_id:
                continue
            connections = service.storage.provider_connections(
                identity_id=account.identity_id,
            )
            if (type(connections) is not list or len(connections) > 512
                    or sum(
                        type(conn) is dict
                        and conn.get("identity_id") == account.identity_id
                        and conn.get("provider_id") == provider
                        and conn.get("credential_id") == credential
                        and conn.get("enabled") is True
                        for conn in connections
                    ) != 1
                    or sum(
                        type(conn) is dict
                        and conn.get("identity_id") == account.identity_id
                        and conn.get("provider_id") == provider
                        and conn.get("credential_id") == credential
                        for conn in connections
                    ) != 1):
                continue
            session = service._sessions.get(
                service._session_key(provider, credential),
            )
            state = _inert_session_state(session)
            session_accounts = _inert_session_accounts(session)
            if (state is None or session_accounts is None
                    or state.get("connected") is not True
                    or state.get("_closed") is True
                    or state.get("provider_id") != provider
                    or state.get("credential_id") != credential
                    or sum(a is account for a in session_accounts) != 1):
                continue
            manager = service._plugin_manager
            plugin_id = service._provider_plugin_ids.get(provider)
            if type(plugin_id) is not str or not plugin_id or manager is None:
                continue
            status = manager.status(plugin_id)
            declaration = status.descriptor
            enabled_for = getattr(manager, "enabled_for_identity", None)
            if (status.enabled is not True or status.state != "ready"
                    or declaration.id != plugin_id
                    or declaration.type != "provider.library"
                    or not callable(enabled_for)
                    or enabled_for(plugin_id, account.identity_id) is not True):
                continue
            fact = assess_lending_operation(
                "loan.borrow",
                manifest_capabilities=declaration.capabilities,
                descriptor=state.get("descriptor"), session=session,
                account=account,
            )
            if (fact.usable is not True
                    or service.storage.metadata_get(
                        borrow_claim_key(account, item),
                    ) is not None
                    or service.storage.loan_borrow_state(
                        borrow_claim_key(account, item),
                    ) is not None):
                continue
            if (service.identity_by_id(requesting_identity_id) is not requester
                    or service._account_context_epoch != context_epoch
                    or service._source_context_epoch != source_epoch
                    or service._sessions.get(
                        service._session_key(provider, credential)) is not session):
                return ()  # ABA/rebind invalidates the *whole* list.
            lender = service.identity_by_id(account.identity_id)
            label = _lender_label(getattr(lender, "label", None))
            result.append(PossibleBorrowLender(
                label=label, lender_identity_id=account.identity_id,
                _account=account, _notice=item,
            ))
            seen.add(route)
        except Exception:
            # One malformed/offline provider cannot suppress unrelated lenders.
            continue
    try:
        if (service.identity_by_id(requesting_identity_id) is not requester
                or service._account_context_epoch != context_epoch
                or service._source_context_epoch != source_epoch):
            return ()
    except Exception:
        return ()
    return _label_lenders(result)


def _label_lenders(result: list[PossibleBorrowLender]) -> tuple[PossibleBorrowLender, ...]:
    # Multiple credentials of the same lender cannot be shown as identical
    # actionable-looking choices. Stable private sorting and public, bounded
    # account labels distinguish them without exposing the credential key.
    ordered = sorted(result, key=lambda row: (
        row.lender_identity_id, row._account.provider_id,
        row._account.credential_id, row._account.unique_id,
        row._notice.docbase, row._notice.record_id,
        row._notice.source_collection or "",
    ))
    frequencies = Counter(row.label for row in ordered)
    public_accounts = {
        id(row): _display_account_label(row._account.display_name)[:32]
        for row in ordered
    }
    pairs = Counter((row.label, public_accounts[id(row)]) for row in ordered)
    ordinals: Counter[str] = Counter()
    visible = []
    for row in ordered:
        if frequencies[row.label] == 1:
            visible.append(row)
            continue
        account_label = public_accounts[id(row)]
        if pairs[(row.label, account_label)] > 1:
            ordinals[row.label] += 1
            suffix = (
                f" · {account_label} · choix "
                f"{ordinals[row.label]}/{frequencies[row.label]}"
            )
        else:
            suffix = f" · {account_label}"
        safe_prefix = row.label[:max(1, 80 - len(suffix))].rstrip()
        visible.append(replace(row, label=safe_prefix + suffix))
    # Composed public labels can collide with another profile's literal
    # name (or with another truncated prefix). Number the entire ordered
    # set only in that case, without ever displaying private route keys.
    if len({row.label for row in visible}) != len(visible):
        numbered = []
        for index, row in enumerate(visible, 1):
            prefix = f"Choix {index}/{len(visible)} · "
            numbered.append(replace(
                row, label=prefix + row.label[:80 - len(prefix)].rstrip(),
            ))
        visible = numbered
    return tuple(visible)


__all__ = ["PossibleBorrowLender", "discover_possible_borrow_lenders",
           "possible_lender_matches"]
