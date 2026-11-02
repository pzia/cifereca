"""Pure descriptor-driven linked-borrower preferences (#4065).

No GTK, provider calls or account enumeration I/O. A connection's identity
and credential scope must match an already observed Account exactly.
"""
from __future__ import annotations

import re

MAX_BORROWERS = 32
MAX_BORROWER_ID = 128


def decimal_limit(value: object, spec: object) -> bool:
    return (type(value) is str
            and re.fullmatch(r"(0|[1-9][0-9]{0,2})", value) is not None
            and type(getattr(spec, "min_value", None)) is int
            and type(getattr(spec, "max_value", None)) is int
            and 0 <= spec.min_value <= int(value) <= spec.max_value <= 999)


def _valid_borrower_id(value: object) -> bool:
    return (type(value) is str and 1 <= len(value) <= MAX_BORROWER_ID
            and value.isprintable() and value == value.strip())


def validate_saved_borrower_limits(raw: object, spec: object) -> dict[str, dict[str, str]]:
    """Reject malformed existing preferences instead of overwriting on Save."""
    if type(raw) is not dict or len(raw) > MAX_BORROWERS:
        raise ValueError("invalid_scoped_options")
    branches = getattr(spec, "branches", ())
    if (type(branches) is not tuple or not branches
            or len(branches) > 4 or any(type(pair) is not tuple
            or len(pair) != 2 or pair[0] not in {"digital", "physical"}
            or type(pair[1]) is not str for pair in branches)
            or len({pair[0] for pair in branches}) != len(branches)
            or not decimal_limit(getattr(spec, "default", None), spec)):
        raise ValueError("invalid_scoped_schema")
    allowed = {pair[0] for pair in branches}
    result = {}
    for borrower, settings in raw.items():
        if not _valid_borrower_id(borrower) or type(settings) is not dict:
            raise ValueError("invalid_scoped_options")
        if any(type(k) is not str or k not in allowed
               or not decimal_limit(v, spec) for k, v in settings.items()):
            raise ValueError("invalid_scoped_options")
        result[borrower] = dict(settings)
    return result


def linked_borrower_choices(
    saved: object, spec: object, observed: object, *,
    provider_id: str, credential_id: str, identity_id: str,
) -> tuple[tuple[str, str], ...]:
    """Persisted readers remain editable offline; live readers are scoped."""
    known = validate_saved_borrower_limits(saved, spec)
    names = {borrower: "" for borrower in known}
    if type(observed) in (list, tuple) and len(observed) <= 256:
        for account in observed:
            borrower = getattr(account, "unique_id", None)
            if (getattr(account, "is_primary", None) is not False
                    or getattr(account, "provider_id", None) != provider_id
                    or getattr(account, "credential_id", None) != credential_id
                    or getattr(account, "identity_id", None) != identity_id
                    or not _valid_borrower_id(borrower)):
                continue
            names.setdefault(borrower, "")
            name = getattr(account, "display_name", "")
            if (type(name) is str and 1 <= len(name.strip()) <= 80
                    and name.isprintable() and "://" not in name
                    and not any(marker in name.casefold() for marker in (
                        "token=", "password=", "authorization:", "cookie:",
                        "bearer ", "secret=", "api_key=", "signature=",
                    ))):
                names[borrower] = name.strip()
    if len(names) > MAX_BORROWERS:
        raise ValueError("too_many_scoped_options")
    return tuple(
        (borrower, f"Compte lié {index}" +
         (f" · {names[borrower]}" if names[borrower] else ""))
        for index, borrower in enumerate(sorted(names), start=1)
    )
