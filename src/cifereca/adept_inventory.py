"""Public ADEPT inventory matching; never inspect the private registry."""
from pathlib import Path


def match_local_book(path, inventories):
    """Return all exact canonical path matches, retaining profile namespaces."""
    canonical = Path(path).resolve()
    matches = []
    for profile in inventories.get('profiles', []):
        for loan in profile.get('loans', []):
            local = loan.get('local_book')
            if isinstance(local, str) and local and Path(local).is_absolute() and Path(local).resolve() == canonical:
                matches.append({'profile': profile['profile'], 'loan': loan})
    return matches
