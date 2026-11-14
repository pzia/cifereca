"""#6002: read-only Home projection of digital loan *uncertainty*.

No provider method, refresh, HTTP, SQLite write, schema migration or GTK
selection is touched. A previous successful Sync run and positive cached
loans cannot demonstrate a complete *current* borrower/branch inventory.
"""
from __future__ import annotations

import re
from collections import defaultdict
from inspect import getattr_static

from ..models import Account, Loan
from ..plugin_api.digital_loan_availability import DigitalLoanAvailability
from ..plugin_api.loan_inventory import LoanInventoryScope, _loan_matches_scope
from ..providers.base import ProviderDescriptor


_MAX_CONNECTIONS = 128
_MAX_BORROWERS = 256
_MAX_LOANS = 10000


def _safe_label(value, fallback: str) -> str:
    if (type(value) is str and 1 <= len(value) <= 80 and value == value.strip()
            and value.isprintable() and not any(
                token in value.casefold() for token in (
                    "http://", "https://", "token=", "password=", "secret=",
                    "cookie=", "bearer ",
                )
            )):
        return value
    return fallback


def _ceiling(raw, *, lower: int, upper: int):
    # The descriptor's own min/max applies. Missing explicit values use the
    # declared default, but corrupted saved values never fall back silently.
    if (type(raw) is not str
            or re.fullmatch(r"(0|[1-9][0-9]{0,2})", raw) is None
            or type(lower) is not int or type(upper) is not int
            or lower < 0 or upper > 99 or upper < lower):
        return None
    number = int(raw)
    return number if lower <= number <= upper else None


def _personal_limit(configured, descriptor, account):
    if type(descriptor) is not ProviderDescriptor:
        return None
    options = configured.get("options")
    if type(options) is not dict:
        return None
    if account.is_primary is True:
        specs = [
            field for field in descriptor.account_options
            if field.advisory_loan_medium == "digital"
            and field.value_type == "unsigned_integer"
        ]
        if len(specs) != 1:
            return None
        spec = specs[0]
        raw = options[spec.key] if spec.key in options else spec.default
        return _ceiling(raw, lower=spec.min_value, upper=spec.max_value)
    if account.is_primary is not False or not account.unique_id:
        return None
    specs = [
        spec for spec in descriptor.borrower_scoped_options
        if spec.advisory_loan_limit is True
        and any(medium == "digital" for medium, _label in spec.branches)
    ]
    if len(specs) != 1:
        return None
    spec = specs[0]
    nested = options.get(spec.key, {})
    if type(nested) is not dict:
        return None
    per_borrower = nested.get(account.unique_id, {})
    if type(per_borrower) is not dict:
        return None
    raw = per_borrower["digital"] if "digital" in per_borrower else spec.default
    return _ceiling(raw, lower=spec.min_value, upper=spec.max_value)


def digital_loan_availability(service, identity_id: str) -> tuple[DigitalLoanAvailability, ...]:
    """Current profile only; enumerate exact configured connections then borrowers.

    The display has only UNKNOWN cases until a durable typed, fresh, complete
    inventory + independent server quota producer exists. This is deliberate:
    a manual 4 and a cache of 0/2 loans can never justify "4/2 available".
    """
    if (type(identity_id) is not str or not identity_id
            or len(identity_id) > 128 or not identity_id.isprintable()):
        raise ValueError("identity_id_required")
    try:
        configured = service.config.provider_accounts()
        descriptors = service.provider_descriptors()
    except Exception:
        return ()
    if (type(configured) is not list or len(configured) > _MAX_CONNECTIONS
            or type(descriptors) not in (list, tuple) or len(descriptors) > 256):
        return ()
    # Never select the last descriptor for a duplicated provider ID:
    # capability and advisory ceiling declarations would then depend on
    # registration order. Only this provider becomes unverified.
    by_descriptor = {}
    duplicate_descriptors = set()
    for desc in descriptors:
        if type(desc) is not ProviderDescriptor or type(desc.id) is not str:
            continue
        if desc.id in by_descriptor:
            duplicate_descriptors.add(desc.id)
        else:
            by_descriptor[desc.id] = desc
    live_accounts = getattr(service, "accounts", ())
    if type(live_accounts) not in (list, tuple) or len(live_accounts) > _MAX_BORROWERS:
        live_accounts = ()
    cache = getattr(service, "last_loans", ())
    if type(cache) not in (list, tuple) or len(cache) > _MAX_LOANS:
        cache = ()

    # Session registry keys are global (provider lowercased + credential),
    # even when a saved connection claims a different profile. Such a reused
    # key cannot safely identify a single owner's connected session. Inspect
    # only bounded configuration metadata; never resolve provider credentials.
    first_owners = {}
    shared_registry_keys = set()
    for entry in configured:
        if type(entry) is not dict:
            continue
        owner = entry.get("identity_id")
        provider, credential = entry.get("provider"), entry.get("id")
        if (type(owner) is not str or not owner
                or type(provider) is not str or not provider
                or type(credential) is not str or not credential
                or len(provider) > 128 or len(credential) > 128):
            continue
        registry_key = (provider.lower(), credential)
        first = first_owners.setdefault(registry_key, owner)
        if first != owner:
            shared_registry_keys.add(registry_key)

    eligible = []
    seen = set()
    ambiguous_connections = set()
    for entry in configured:
        if type(entry) is not dict or entry.get("identity_id") != identity_id:
            continue
        provider, credential = entry.get("provider"), entry.get("id")
        if (type(provider) is not str or not provider
                or type(credential) is not str or not credential
                or len(provider) > 128 or len(credential) > 128
                or entry.get("enabled", True) not in (True, False)
                or type(entry.get("enabled", True)) is not bool):
            continue
        # The session registry lowercases provider IDs. Two differently
        # cased spellings with the same credential are ONE ambiguous session,
        # never two independent borrower or advisory-capacity claims.
        registry_key = (provider.lower(), credential)
        if registry_key in seen:
            ambiguous_connections.add(registry_key)
            continue
        seen.add(registry_key)
        descriptor = by_descriptor.get(provider)
        if (provider not in duplicate_descriptors
                and descriptor is not None
                and descriptor.capabilities.loans is not True):
            continue  # not a declared lender, e.g. Kobo/Numilog store
        eligible.append((entry, descriptor))
    if not eligible:
        return ()

    rows = []
    counts = defaultdict(int)
    for configured_entry, descriptor in eligible:
        provider = configured_entry["provider"]
        credential = configured_entry["id"]
        counts[provider] += 1
        number = counts[provider]
        provider_label = _safe_label(
            descriptor.display_name if descriptor else None,
            "Fournisseur non qualifié",
        )
        connection_label = f"Connexion {number}"
        configured_label = _safe_label(configured_entry.get("label"), "")
        if configured_label:
            connection_label += " · " + configured_label
        if ((provider.lower(), credential) in ambiguous_connections
                or (provider.lower(), credential) in shared_registry_keys
                or provider in duplicate_descriptors):
            # Neither an ambiguous global session key nor an ambiguous
            # descriptor can qualify an exact borrower's personal limit.
            rows.append(DigitalLoanAvailability(
                identity_id=identity_id, provider_id=provider,
                credential_id=credential, borrower_id=None,
                provider_label=(
                    "Fournisseur non qualifié"
                    if provider in duplicate_descriptors else provider_label
                ),
                connection_label=connection_label,
                borrower_label="Emprunteur non vérifié",
                reason=("provider_unverified" if provider in duplicate_descriptors
                        else "scope_ambiguous"),
            ))
            continue
        matches = [
            a for a in live_accounts
            if type(a) is Account and a.identity_id == identity_id
            and a.provider_id == provider and a.credential_id == credential
        ]
        # A disconnected or not-yet-authenticated account may have zero
        # borrowers in memory; DO NOT assume a primary or the first profile.
        if not matches:
            rows.append(DigitalLoanAvailability(
                identity_id=identity_id, provider_id=provider,
                credential_id=credential, borrower_id=None,
                provider_label=provider_label, connection_label=connection_label,
                borrower_label="Emprunteur non vérifié",
                reason=("account_disabled" if configured_entry.get("enabled", True) is False
                        else "borrower_unverified"),
            ))
            continue
        if (len(matches) > 32 or any(
                type(a.unique_id) is not str
                or len(a.unique_id) > 1024
                or "\x00" in a.unique_id
                or type(a.is_primary) is not bool
                # The empty upstream ID is reserved for the primary borrower.
                or (a.is_primary is False and not a.unique_id)
                for a in matches)
                # One connection must never present two distinct primary
                # borrowers as independent digital loan capacities.
                or sum(a.is_primary is True for a in matches) > 1
                # Distinct borrowers must have distinct exact upstream IDs;
                # repeat IDs must not create duplicate Home capacity lines.
                or len({a.unique_id for a in matches}) != len(matches)):
            # A malformed or oversized borrower inventory invalidates ONLY
            # this configured connection. Never sort an untrusted identifier,
            # pick the first account, or suppress other independent lenders.
            rows.append(DigitalLoanAvailability(
                identity_id=identity_id, provider_id=provider,
                credential_id=credential, borrower_id=None,
                provider_label=provider_label, connection_label=connection_label,
                borrower_label="Emprunteur non vérifié", reason="scope_ambiguous",
            ))
            continue
        for borrower in sorted(matches, key=lambda a: (
                a.is_primary is not True, a.unique_id)):
            try:
                scope = LoanInventoryScope.for_account(borrower, descriptor=descriptor)
            except ValueError:
                scope = None
            observed = None
            if scope is not None:
                active = {
                    loan.id for loan in cache
                    if type(loan) is Loan and loan.medium == "digital"
                    and loan.state == "active" and _loan_matches_scope(loan, scope)
                }
                if active:
                    observed = len(active)
            connected = False
            try:
                session_key = service._session_key(provider, credential)
                session = service._sessions.get(session_key)
                connected = (
                    session is not None
                    and getattr_static(session, "connected", None) is True
                    # A revoked/closed session may retain a stale connected
                    # flag. Inspect its static marker without invoking
                    # properties, provider methods or a new network request.
                    and getattr_static(session, "_closed", False) is False
                    and getattr_static(session, "provider_id", None) == provider
                    and getattr_static(session, "credential_id", None) == credential
                )
            except Exception:
                pass
            reason = (
                "scope_ambiguous" if scope is None else
                "provider_unverified" if descriptor is None else
                "account_disabled" if configured_entry.get("enabled", True) is False else
                "account_disconnected" if not connected else
                "inventory_unverified"
            )
            branch_label = "Compte principal" if borrower.is_primary is True else "Compte lié"
            display = _safe_label(borrower.display_name, "")
            label = f"{branch_label} · {display}" if display else branch_label
            rows.append(DigitalLoanAvailability(
                identity_id=identity_id, provider_id=provider,
                credential_id=credential, borrower_id=borrower.unique_id,
                provider_label=provider_label, connection_label=connection_label,
                borrower_label=label,
                observed_loans=observed,
                personal_ceiling=(
                    _personal_limit(configured_entry, descriptor, borrower)
                    if scope is not None and descriptor is not None else None
                ),
                reason=reason,
            ))
    return tuple(rows)


__all__ = ["digital_loan_availability"]
