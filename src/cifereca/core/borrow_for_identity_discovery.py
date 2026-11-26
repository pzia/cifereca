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
from .borrow_action_scope import _fields, _valid_fields, borrow_claim_key
from .borrow_for_identity_live import _lender_label
from .borrow_for_identity_preview import _display_account_label
from .loan_action_scope import _inert_session_accounts, _inert_session_state


def _notice_binding(account: Account, notice: SearchResult):
    """Extra exact source coordinates, separate from the durable replay key.

    A revision change must revoke a displayed selection, but must not mint
    a second borrow claim for the same upstream borrower/record.
    """
    # Read-only catalogue mirrors are not native lender occurrences.
    # Keep the same distinction as manual_borrow_contract, including on
    # the held candidate at click time. No extra eligibility requirement.
    metadata = notice.metadata
    if (type(metadata) is not dict or len(metadata) > 256
            or any(type(key) is not str for key in metadata)):
        return None
    access = metadata.get("catalogue_access")
    provenance = metadata.get("search_provenance")
    if (type(access) not in (str, type(None))
            or type(provenance) not in (str, type(None))
            or access == "shared_read_only"
            or provenance == "known_catalog_stale"):
        return None
    scenario, revision, source_account = (
        notice.scenario, notice.revision_id, notice.source_account_id,
    )
    if (type(scenario) is not str or not 0 < len(scenario) <= 1024
            or not scenario.isprintable()
            or (revision is not None and (
                type(revision) is not str or not 0 < len(revision) <= 1024
                or not revision.isprintable()))
            or (source_account is not None and (
                type(source_account) is not str or len(source_account) > 1024
                or (source_account and not source_account.isprintable())
                or source_account != account.unique_id))):
        return None
    return scenario, revision, source_account


@dataclass(frozen=True, slots=True)
class PossibleBorrowLender:
    """Inert local account candidate, never an upstream offer or permission."""

    label: str
    lender_identity_id: str = field(repr=False)
    _account: Account = field(repr=False, compare=False)
    _notice: SearchResult = field(repr=False, compare=False)
    supplier_eligibility: Literal["not_verified"] = "not_verified"
    executable: bool = False
    # A discovery receipt cannot be silently rebound to a replacement
    # lender session, even if account coordinates and Core epochs agree.
    # UI-only test descriptors may omit it; Core execution never may.
    _session: object = field(default=None, repr=False, compare=False)
    _session_generation: object = field(default=None, repr=False, compare=False)
    # A SearchResult and Account can be mutated in-place after rendering;
    # object identity alone is not an immutable edition/source snapshot.
    _captured_fields: tuple[object, ...] = field(
        init=False, repr=False, compare=False,
    )

    _captured_notice_binding: tuple[object, ...] = field(
        init=False, repr=False, compare=False,
    )
    _captured_label: str = field(init=False, repr=False, compare=False)

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
                or borrow_claim_key(account, notice) is None
                or _notice_binding(account, notice) is None):
            raise ValueError("invalid_possible_borrow_lender")
        object.__setattr__(self, "_captured_fields", _fields(account, notice))
        object.__setattr__(self, "_captured_notice_binding", _notice_binding(account, notice))
        object.__setattr__(self, "_captured_label", self.label)



def possible_lender_matches(candidate, notice, *, requesting_identity_id, include_requester=False):
    """Pure check of an already captured inert lender, never permission.

    A notice or account can change in place while a discovery task is queued,
    or while a chooser is open. An object reference alone is not a receipt.
    """
    if (type(candidate) is not PossibleBorrowLender
            or type(notice) is not SearchResult
            or candidate._notice is not notice
            or candidate.executable is not False
            or candidate.supplier_eligibility != "not_verified"
            # Frozen dataclasses can be rewritten via object.__setattr__.
            # A changed label could misattribute the exact lender at consent.
            or type(candidate.label) is not str
            or candidate.label != candidate._captured_label
            or type(requesting_identity_id) is not str or not requesting_identity_id):
        return False
    account = candidate._account
    if (type(account) is not Account or account.is_primary is not True
            or type(candidate.lender_identity_id) is not str
            or (candidate.lender_identity_id == requesting_identity_id and include_requester is not True)
            or type(candidate._captured_fields) is not tuple
            or not _valid_fields(candidate._captured_fields)):
        return False
    fields = _fields(account, notice)
    binding = _notice_binding(account, notice)
    return (binding is not None
            and type(candidate._captured_notice_binding) is tuple
            and binding == candidate._captured_notice_binding
            and _valid_fields(fields)
            and candidate.lender_identity_id == fields[0]
            and candidate._captured_fields == fields)


def possible_lender_claims_distinct(rows, notice, *, requesting_identity_id):
    """One exact ordinary-borrow claim cannot be listed as multiple lenders.

    Different dataclass receipts can refer to the same borrower/edition.
    This is only a bounded read-only menu invariant, never admission.
    """
    if type(rows) is not tuple or not 1 <= len(rows) <= 64:
        return False
    seen = set()
    for row in rows:
        if not possible_lender_matches(
                row, notice, requesting_identity_id=requesting_identity_id):
            return False
        claim = borrow_claim_key(row._account, notice)
        if claim is None or claim in seen:
            return False
        seen.add(claim)
    return True


def possible_lender_session_matches(candidate, session):
    """Worker-only check of the original inert session receipt."""
    if (type(candidate) is not PossibleBorrowLender
            or session is None or candidate._session is not session):
        return False
    state = _inert_session_state(session)
    registered = _inert_session_accounts(session)
    fields = candidate._captured_fields
    if (state is None or registered is None
            or type(fields) is not tuple or not _valid_fields(fields)):
        return False
    generation = state.get("_epoch")
    expected = candidate._session_generation
    # Session epoch alone cannot attest a still-connected borrower. A
    # disconnect, closed session or in-place credential rebind may happen
    # after discovery without rotating the Core account context.
    return (state.get("connected") is True
            and state.get("_closed") is not True
            and state.get("provider_id") == fields[1]
            and state.get("credential_id") == fields[2]
            and sum(account is candidate._account for account in registered) == 1
            and type(generation) in (int, str, type(None))
            and type(expected) is type(generation)
            and expected == generation)


def _unique_lender_account(accounts, provider, credential, requester, *, include_requester=False):
    """Reapply the same inert inventory rule at admission and publication."""
    if type(accounts) not in (list, tuple) or len(accounts) > 512:
        return None
    matching = [
        a for a in accounts
        if (type(a) is Account and a.is_primary is True
            and (a.identity_id != requester or include_requester is True)
            and a.provider_id == provider
            and a.credential_id == credential)
    ]
    if len(matching) != 1:
        return None
    account = matching[0]
    # A provider+credential session key is not scoped by profile.
    if any(
        type(other) is not Account
        or (other.provider_id == provider
            and other.credential_id == credential
            and other.identity_id != account.identity_id)
        for other in accounts
    ):
        return None
    return account


def discover_possible_borrow_lenders(
    service: Any, *, requesting_identity_id: str,
    notices: tuple[SearchResult, ...], include_requester: bool = False,
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

    result: dict[tuple[str, ...], PossibleBorrowLender | None] = {}
    # Compare each admitted session again after the entire inventory, not
    # merely after its individual capability read. A later source can
    # invalidate an earlier one without changing the navigation epoch.
    admitted_sessions = {}
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
            account = _unique_lender_account(
                accounts, provider, credential, requesting_identity_id,
                include_requester=include_requester,
            )
            if account is None:
                continue
            # A native C roster includes its reader only through the ordinary
            # direct action policy. Legacy cross-profile callers remain unchanged.
            if account.identity_id == requesting_identity_id:
                from .manual_borrow_contract import manual_borrow_action_available
                if not manual_borrow_action_available(service, item, identity_id=requesting_identity_id):
                    continue
            route = (
                account.identity_id, provider, credential, account.unique_id,
                item.docbase, item.record_id, item.source_collection or "",
            )
            binding = _notice_binding(account, item)
            if binding is None or borrow_claim_key(account, item) is None:
                continue
            if route in result:
                previous = result[route]
                # One stable anti-replay route, but two source observations
                # disagree on the exact revision/scenario/account. Never
                # select the first notice just because it arrived first.
                if previous is not None and previous._captured_notice_binding != binding:
                    result[route] = None
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
            session_generation = state.get('_epoch')
            descriptor = state.get('descriptor')
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
            # Name the issuing library, not merely the lender profile.
            # Use the already validated installed descriptor, never a
            # supplier URL, credential, account ID or notice metadata.
            profile_label = _lender_label(getattr(lender, "label", None))
            library_label = _display_account_label(state["descriptor"].display_name)
            label = f"{profile_label[:32].rstrip()} sur {library_label[:32].rstrip()}"
            result[route] = PossibleBorrowLender(
                label=label, lender_identity_id=account.identity_id,
                _account=account, _notice=item,
                _session=session, _session_generation=session_generation,
            )
            admitted_sessions[route] = (session, session_generation, descriptor, lender)
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
    # One inert, bounded pass over the admitted set: no new provider or SQL
    # call. Any change of an earlier source aborts this mixed snapshot.
    try:
        final_accounts = service.accounts
        if type(final_accounts) not in (list, tuple) or len(final_accounts) > 512:
            return ()
        final_accounts = tuple(final_accounts)
        for route, row in result.items():
            if row is None:
                continue
            session, generation, descriptor, lender = admitted_sessions[route]
            account, notice = row._account, row._notice
            state = _inert_session_state(session)
            registered = _inert_session_accounts(session)
            if (not possible_lender_matches(row, notice, requesting_identity_id=requesting_identity_id,
                                               include_requester=include_requester)
                    or service.identity_by_id(row.lender_identity_id) is not lender
                    or service._sessions.get(service._session_key(
                        account.provider_id, account.credential_id)) is not session
                    or state is None or registered is None
                    or state.get('connected') is not True or state.get('_closed') is True
                    or state.get('descriptor') is not descriptor
                    or state.get('provider_id') != account.provider_id
                    or state.get('credential_id') != account.credential_id
                    or type(state.get('_epoch')) is not type(generation)
                    or state.get('_epoch') != generation
                    or sum(a is account for a in registered) != 1
                    or _unique_lender_account(
                        final_accounts, account.provider_id, account.credential_id,
                        requesting_identity_id, include_requester=include_requester) is not account):
                return ()
        visible = _label_lenders([row for row in result.values() if row is not None])
        # Label composition can rebuild a frozen DTO. Never re-admit mutated
        # source/account receipts while preparing the final visible options.
        if any(
            not possible_lender_matches(
                row, row._notice,
                requesting_identity_id=requesting_identity_id,
                include_requester=include_requester,
            ) or not possible_lender_session_matches(row, row._session)
            for row in visible
        ):
            return ()
        if (service.identity_by_id(requesting_identity_id) is not requester
                or service._account_context_epoch != context_epoch
                or service._source_context_epoch != source_epoch):
            return ()
        return visible
    except Exception:
        return ()


def _relabel_preserving_receipt(
    row: PossibleBorrowLender, label: str,
) -> PossibleBorrowLender:
    """Changing a public label cannot refresh an exact source receipt.

    dataclasses.replace invokes __post_init__, which otherwise recaptures
    mutable Account/SearchResult fields during a display-only operation.
    """
    renamed = replace(row, label=label)
    object.__setattr__(renamed, "_captured_fields", row._captured_fields)
    object.__setattr__(
        renamed, "_captured_notice_binding", row._captured_notice_binding,
    )
    return renamed


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
        visible.append(_relabel_preserving_receipt(row, safe_prefix + suffix))
    # Composed public labels can collide with another profile's literal
    # name (or with another truncated prefix). Number the entire ordered
    # set only in that case, without ever displaying private route keys.
    if len({row.label for row in visible}) != len(visible):
        numbered = []
        for index, row in enumerate(visible, 1):
            prefix = f"Choix {index}/{len(visible)} · "
            numbered.append(_relabel_preserving_receipt(
                row, prefix + row.label[:80 - len(prefix)].rstrip(),
            ))
        visible = numbered
    return tuple(visible)


__all__ = ["PossibleBorrowLender", "discover_possible_borrow_lenders",
           "possible_lender_matches", "possible_lender_claims_distinct"]
