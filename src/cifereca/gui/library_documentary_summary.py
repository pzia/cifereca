"""Presentation of qualified Library-snapshot counts, not Sync history."""

_LABELS = (
    ("available", "fiches disponibles"),
    ("pending", "en attente"),
    ("running", "réservées"),
    ("retry_wait", "différées"),
    ("failed", "non abouties"),
    ("proof_unavailable", "preuves à revalider"),
    ("paused", "requêtes désactivées"),
    ("not_attested", "candidats non attestés"),
)


def documentary_summary(counts):
    if type(counts) is not dict:
        return ""
    base_keys = {key for key, _ in _LABELS}
    refresh_keys = {"refresh_" + key for key in base_keys - {"available", "proof_unavailable"}}
    if (set(counts) - base_keys - refresh_keys
            or any(type(n) is not int or not 1 <= n <= 10000 for n in counts.values())
            or sum(counts.get(key, 0) for key in base_keys) > 10000
            or sum(counts.get(key, 0) for key in refresh_keys) > counts.get("available", 0)):
        return ""
    parts = [f"{counts[key]} {label}" for key, label in _LABELS if key in counts]
    if not parts:
        return ""
    text = "Enrichissement documentaire (achats qualifiés) : " + " · ".join(parts)
    refresh = [f"{counts['refresh_' + key]} {label}" for key, label in _LABELS
               if "refresh_" + key in refresh_keys and "refresh_" + key in counts]
    if refresh:
        text += "\nFiches conservées, dont actualisation : " + " · ".join(refresh)
    return text
