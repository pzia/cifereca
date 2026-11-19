"""Bounded closed counters of a historic A-to-C handoff, not execution."""
_FIELDS = ("requested", "admitted", "deferred", "unrequested")


def closed_handoff_counts(raw):
    if (type(raw) is not dict or set(raw) != set(_FIELDS)
            or any(type(raw[key]) is not int or not 0 <= raw[key] <= 10_000
                   for key in _FIELDS)
            or raw["admitted"] + raw["deferred"] > raw["requested"]
            or raw["requested"] + raw["unrequested"] > 10_000):
        return None
    return {key: raw[key] for key in _FIELDS}


# Current projected snapshot, not historic handoff counters or a scheduler.
# One exact purchase source may have several local/remote presentations.
_LIVE_GROUPS = {
    "pending": "pending", "running": "running", "retry_wait": "retry_wait",
    "failed": "failed", "proof_unavailable": "proof_unavailable", "paused": "paused",
    "direct_locator_unattested": "not_attested", "comparison_title_unattested": "not_attested",
    "candidate_unqualified": "not_attested",
}
_MAX_ROWS = 10_000


def projected_purchase_counts(rows, *, identity_id):
    """Count distinct qualified purchase sources within this Library snapshot.

    No source coverage assertion, unknown total, I/O, or inferred failure.
    Ambiguous keys and conflicting packets for a source are excluded; other
    identities are ignored. A retained public notice is available evidence,
    not a claim that every field is populated or the refresh succeeded.
    """
    from collections import Counter
    from .notice_c_purchase_library_projection import purchase_library_notice, purchase_library_status

    if (type(rows) is not list or not 1 <= len(rows) <= _MAX_ROWS
            or type(identity_id) is not str or not identity_id):
        return {}
    multiplicities = Counter(row["key"] for row in rows
                             if type(row) is dict and type(row.get("key")) is str)
    states = {}
    for row in rows:
        if (type(row) is not dict or row.get("identity_id") != identity_id
                or type(row.get("key")) is not str or multiplicities[row["key"]] != 1):
            continue
        notice = purchase_library_notice(row)
        status = purchase_library_status(row)
        if notice is not None and status is not None:
            # The projection guarantees these are mutually exclusive.
            states.setdefault(notice.source, set()).add(None)
            states.setdefault(status.source, set()).add(None)
            continue
        if notice is not None:
            source, state = notice.source, "available"
        elif status is not None:
            source, state = status.source, _LIVE_GROUPS.get(status.state)
        else:
            continue
        if source.scope.identity_id == identity_id:
            states.setdefault(source, set()).add(state)
    return dict(Counter(next(iter(group)) for group in states.values()
                        if len(group) == 1 and None not in group))
