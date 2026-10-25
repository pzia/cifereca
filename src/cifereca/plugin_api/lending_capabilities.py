"""Non-mutating lending capability assessment (Core/Plugin API P1 #3120).

Availability is NEVER loan-specific permission, consent or proof of a remote
effect. This adapter only reads existing manifest/descriptor/session facts and
method declarations; it cannot invoke a provider operation or touch the net.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cifereca.models import Account
from cifereca.providers.base import ProviderSession, ProviderDescriptor

LendingAvailability = Literal["supported", "unsupported", "unavailable", "unknown"]

# Legacy manifest capabilities remain valid in contract cifereca.provider/1.
# New return/renew capabilities are OPTIONAL: an old provider with working
# methods must not be automatically promoted or broken by this read-only check.
_OPERATION_REQUIREMENTS = {
    "loan.list": ("sync.loans", "loans", "loans"),
    "loan.borrow": ("acquisition.borrow", "borrow", "borrow"),
    "loan.download_token": ("acquisition.download", "digital_download", "download_digital"),
    "loan.renew": ("lending.renew", "renew_loan", "renew"),
    "loan.return": ("lending.return", "return_loan", "return_digital"),
}
_MUTATING = frozenset({"loan.borrow", "loan.renew", "loan.return", "loan.download_token"})


@dataclass(frozen=True, slots=True)
class LendingOperationFact:
    operation: str
    availability: LendingAvailability
    reason_code: str
    manifest_declared: bool
    descriptor_declared: bool
    method_implemented: bool
    session_ready: bool
    explicit_action_required: bool

    @property
    def usable(self) -> bool:
        """Only static/runtime availability, NOT consent or borrower rights."""
        return self.availability == "supported"


def assess_lending_operation(
    operation: str, *,
    manifest_capabilities: tuple[str, ...] | list[str] | None,
    descriptor: ProviderDescriptor,
    session: ProviderSession | None,
    account: Account | None,
) -> LendingOperationFact:
    """Report closed non-authorizing evidence for an exact borrower.

    Manifest v1 without a lending capability remains compatible: this call
    returns unsupported/unknown, but changes neither the provider nor any
    pre-existing Core action. No monkey-patch or provider method is called.
    """
    # Never echo unknown operation names, account identifiers or credentials.
    key = operation if type(operation) is str and operation in _OPERATION_REQUIREMENTS else ""
    requirement = _OPERATION_REQUIREMENTS.get(key)
    if requirement is None:
        return LendingOperationFact("unknown", "unknown", "operation_unknown",
                                    False, False, False, False, False)

    manifest_name, descriptor_name, method_name = requirement
    action = key in _MUTATING

    if (type(manifest_capabilities) not in (list, tuple)
            or any(type(c) is not str for c in manifest_capabilities)
            or len(manifest_capabilities) > 128
            or len(set(manifest_capabilities)) != len(manifest_capabilities)):
        return LendingOperationFact(key, "unknown", "manifest_unverified",
                                    False, False, False, False, action)

    declared = manifest_name in manifest_capabilities
    try:
        if type(descriptor) is not ProviderDescriptor:
            raise TypeError
        raw_cap = getattr(descriptor.capabilities, descriptor_name, None)
        if type(raw_cap) is not bool:
            raise TypeError
        desc_claim = raw_cap
        # Only an override of the abstract legacy 'unsupported' method
        # counts as an implementation. A method name alone cannot prove use.
        method = getattr(type(session), method_name, None) if session is not None else None
        base = getattr(ProviderSession, method_name)
        implemented = callable(method) and method is not base
        plugin_ready = descriptor.state == "ready"
        connected = (type(getattr(session, "connected", None)) is bool
                     and session.connected is True) if session is not None else False
        exact_session = (
            session is not None and session.provider_id == descriptor.id
            and account is not None and type(account) is Account
            and account.provider_id == descriptor.id
            and account.credential_id == session.credential_id
            and type(account.identity_id) is str and bool(account.identity_id)
        )
        # The chosen account must exist in THIS live session. A stored
        # credential and a successful connect are not enough by themselves.
        current_accounts = session.accounts if connected and exact_session else ()
        account_ready = (
            type(current_accounts) in (list, tuple)
            and any(type(row) is Account and
                    row.identity_id == account.identity_id and
                    row.provider_id == account.provider_id and
                    row.credential_id == account.credential_id and
                    row.unique_id == account.unique_id
                    for row in current_accounts[:512])
        )
        ready = bool(plugin_ready and connected and exact_session and account_ready)
    except Exception:
        return LendingOperationFact(key, "unknown", "invalid_declaration",
                                    declared, False, False, False, action)

    if declared and not desc_claim:
        state, reason = "unsupported", "manifest_descriptor_conflict"
    elif desc_claim and not declared:
        state, reason = "unknown", "legacy_capability_unmapped"
    elif not declared:
        state, reason = "unsupported", "not_declared"
    elif not implemented:
        state, reason = "unsupported", "method_unimplemented"
    elif not ready:
        state, reason = "unavailable", "session_unavailable"
    else:
        state, reason = "supported", "available_unverified_permission"

    return LendingOperationFact(key, state, reason, declared, desc_claim,
                                implemented, ready, action)


__all__ = ["LendingAvailability", "LendingOperationFact", "assess_lending_operation"]
