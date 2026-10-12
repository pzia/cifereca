"""Read-only projection of existing ADEPT profile references and names.

No profile is created, changed or activated here. Identity bindings and runtime
profile enumeration are explicit dependencies; persisted historical provenance
and the service's borrower/readiness guards remain outside this boundary.
The legacy CLI-reference fallback is intentionally preserved, not strengthened.
"""


def profile_reference(row):
    return str(row.get('profile_id') or row.get('id') or row.get('name') or row.get('profile') or '').strip()


def profile_name(row):
    return str(row.get('name') or row.get('profile') or row.get('profile_id') or row.get('id') or '').strip()


def profile_row(reference, profiles=None, *, list_profiles):
    ref = str(reference or '').strip()
    if not ref:
        return None
    rows = profiles if profiles is not None else list_profiles()
    matches = [row for row in rows if ref in {str(row.get(key) or '').strip() for key in ('profile_id', 'id', 'name', 'profile')}]
    return matches[0] if len(matches) == 1 else None


def profile_cli_name(reference, profiles=None, *, resolve_row, name_of):
    ref = str(reference or '').strip()
    if not ref:
        return None
    row = resolve_row(ref, profiles)
    return name_of(row) if row else ref


def originating_profile_id(identity_id, profile_name, *, binding_get, list_profiles, name_of, reference_of):
    reference = binding_get('identity', identity_id)
    if reference:
        return reference
    try:
        rows = [r for r in list_profiles() if name_of(r) == profile_name]
    except Exception:
        return None
    return reference_of(rows[0]) if len(rows) == 1 else None
