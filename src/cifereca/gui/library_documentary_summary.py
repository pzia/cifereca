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
    if (set(counts) - {key for key, _ in _LABELS}
            or any(type(n) is not int or not 1 <= n <= 10000 for n in counts.values())
            or sum(counts.values()) > 10000):
        return ""
    parts = [f"{counts[key]} {label}" for key, label in _LABELS if key in counts]
    return "Enrichissement documentaire (achats qualifiés) : " + " · ".join(parts) if parts else ""
