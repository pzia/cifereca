"""Generation for explicitly scoped source operations, independent of the UI.

LibraryService revokes it before disconnect/reconnect or configuration changes.
The generation complements exact account/session/SQLite guards; it is never
proof of an owner or permission to acquire. Minimal service facades which do
not manage reconnects have generation zero and still need all binding guards.
"""
from __future__ import annotations


def source_context_epoch(service) -> int:
    epoch = getattr(service, "_source_context_epoch", 0)
    if type(epoch) is not int or epoch < 0:
        raise ValueError("invalid_source_context_epoch")
    return epoch
