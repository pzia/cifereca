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
