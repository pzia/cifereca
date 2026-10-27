"""Non-mutating lending capability assessment (Core/Plugin API P1 #3120).

Availability is NEVER loan-specific permission, consent or proof of a remote
effect. This adapter only reads existing manifest/descriptor/session facts and
method declarations; it cannot invoke a provider operation or touch the net.
"""
from __future__ import annotations

from dataclasses import dataclass
from inspect import getattr_static
from typing import Literal

from cifereca.models import Account
from cifereca.providers.base import ProviderSession, ProviderDescriptor, ProviderCapabilities

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
        if (type(descriptor) is not ProviderDescriptor
                or type(descriptor.capabilities) is not ProviderCapabilities):
            raise TypeError
        # Exact dataclass fields only. A hostile descriptor must never use an
        # arbitrary property to run HTTP during a capability inspection.
        raw_cap = vars(descriptor.capabilities).get(descriptor_name)
        if type(raw_cap) is not bool:
            raise TypeError
        desc_claim = raw_cap
        # A class lookup via getattr can invoke an attacker-controlled
        # metaclass descriptor. Static lookup cannot trigger that method.
        klass = type(session) if session is not None else None
        base = getattr_static(ProviderSession, method_name)
        method = getattr_static(klass, method_name, None) if klass is not None else None
        implemented = (
            isinstance(klass, type)
            and issubclass(klass, ProviderSession)
            and callable(method) and method is not base
        )
        plugin_ready = descriptor.state == "ready"

        # The ProviderSession.accounts property is a method call (even if it
        # looks like a field). Subclasses may replace it with network access.
        # Read the base class's _accounts data directly ONLY when the
        # descriptor is the unchanged safe base property. Custom dynamic
        # account registries are unverified until a separate trusted adapter
        # exists; never invoke them merely to render action availability.
        session_data = None
        # isinstance() on an ABC may evaluate an instance's dynamic
        # __class__ attribute. This *read-only* inspection must never
        # execute a provider __getattribute__ (possibly issuing HTTP).
        # Validate the class, not the instance, before reading raw memory.
        if (klass is not None
                and isinstance(klass, type)
                and issubclass(klass, ProviderSession)
                and getattr_static(klass, "__getattribute__", None)
                    is object.__getattribute__
                and getattr_static(klass, "accounts", None) is
                    ProviderSession.__dict__["accounts"]):
            try:
                session_data = object.__getattribute__(session, "__dict__")
            except (AttributeError, TypeError):
                session_data = None
            if type(session_data) is not dict:
                session_data = None
        if session_data is None:
            session_data = {}
        connected = session_data.get("connected") is True
        provider = session_data.get("provider_id")
        credential = session_data.get("credential_id")
        current_accounts = session_data.get("_accounts")
        account_valid = (
            type(account) is Account
            and all(type(value) is str and len(value) <= 1024
                    and chr(0) not in value
                    for value in (
                        account.provider_id, account.credential_id,
                        account.identity_id, account.unique_id,
                    ))
            and bool(account.identity_id)
        )
        exact_session = bool(
            type(provider) is str and type(credential) is str
            and type(descriptor.id) is str and bool(descriptor.id)
            and provider == descriptor.id
            and account_valid
            and account.provider_id == descriptor.id
            and account.credential_id == credential
        )
        # Bound at most 512 already-known accounts. This is a memory read,
        # NEVER a session.accounts property call or provider refresh.
        # Duplicate borrower registrations must not count as readiness.
        account_ready = bool(
            type(current_accounts) in (list, tuple)
            and len(current_accounts) <= 512
            and exact_session
            and sum(
                1 for row in current_accounts if (
                type(row) is Account
                and all(type(value) is str for value in (
                    row.identity_id, row.provider_id,
                    row.credential_id, row.unique_id,
                ))
                and row.identity_id == account.identity_id
                and row.provider_id == account.provider_id
                and row.credential_id == account.credential_id
                and row.unique_id == account.unique_id
                )
            ) == 1
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


LendingPublicationStatus = Literal[
    "declared", "absent", "legacy_unmapped", "conflict", "unverified",
]


@dataclass(frozen=True, slots=True)
class LendingPublicationFact:
    """Static manifest/descriptor/class agreement, NOT action authorization."""
    operation: str
    publication_status: LendingPublicationStatus
    reason_code: str
    manifest_declared: bool
    descriptor_declared: bool
    method_implemented: bool

    @property
    def publishable(self) -> bool:
        """The static contract is consistent; runtime permission is unchecked."""
        return self.publication_status == "declared"


def validate_lending_publication(
    *,
    manifest_capabilities: tuple[str, ...] | list[str] | None,
    descriptor: ProviderDescriptor,
    session_type: type[ProviderSession],
) -> tuple[LendingPublicationFact, ...]:
    """Audit every canonical lending operation without constructing a session.

    Intended for packaging/static conformance. Does NOT call providers,
    sessions or network services, and never authorizes a CoreUI button.
    Optional legacy capabilities remain unmapped rather than silently
    promoting an internal method to a public lending operation.
    """
    manifest_ok = (
        type(manifest_capabilities) in (tuple, list)
        and len(manifest_capabilities) <= 128
        and all(type(cap) is str for cap in manifest_capabilities)
        and len(set(manifest_capabilities)) == len(manifest_capabilities)
    )
    descriptor_ok = (
        type(descriptor) is ProviderDescriptor
        and type(descriptor.capabilities) is ProviderCapabilities
    )
    class_ok = (
        isinstance(session_type, type)
        and issubclass(session_type, ProviderSession)
    )
    output: list[LendingPublicationFact] = []
    for operation, (manifest_name, capability_name, method_name) in (
        _OPERATION_REQUIREMENTS.items()
    ):
        declared = bool(manifest_ok and manifest_name in manifest_capabilities)
        descriptor_claim = False
        implemented = False
        if manifest_ok and descriptor_ok and class_ok:
            raw_claim = vars(descriptor.capabilities).get(capability_name)
            if type(raw_claim) is not bool:
                status, reason = "unverified", "invalid_declaration"
            else:
                descriptor_claim = raw_claim
                # Reading a function object on a class via getattr_static
                # cannot run a descriptor or the provider's networking code.
                current = getattr_static(session_type, method_name, None)
                base = getattr_static(ProviderSession, method_name, None)
                implemented = callable(current) and current is not base
                if declared and not descriptor_claim:
                    status, reason = "conflict", "manifest_descriptor_conflict"
                elif descriptor_claim and not declared:
                    status, reason = "legacy_unmapped", "legacy_capability_unmapped"
                elif not declared:
                    status, reason = "absent", "not_declared"
                elif not implemented:
                    status, reason = "conflict", "method_unimplemented"
                elif descriptor.state != "ready":
                    status, reason = "unverified", "plugin_not_ready"
                else:
                    status, reason = "declared", "publication_consistent"
        else:
            status = "unverified"
            reason = "manifest_unverified" if not manifest_ok else "invalid_declaration"
        output.append(LendingPublicationFact(
            operation, status, reason, declared, descriptor_claim, implemented,
        ))
    return tuple(output)


__all__ = [
    "LendingAvailability", "LendingOperationFact", "assess_lending_operation",
    "LendingPublicationStatus", "LendingPublicationFact", "validate_lending_publication",
]
