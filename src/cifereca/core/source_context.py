"""Generation for explicitly scoped source operations, independent of the UI.

LibraryService revokes it before disconnect/reconnect or configuration changes.
The generation complements exact account/session/SQLite guards; it is never
proof of an owner or permission to acquire. Minimal service facades which do
not manage reconnects have generation zero and still need all binding guards.
"""
from __future__ import annotations

from contextlib import nullcontext


def source_context_epoch(service) -> int:
    epoch = getattr(service, "_source_context_epoch", 0)
    if type(epoch) is not int or epoch < 0:
        raise ValueError("invalid_source_context_epoch")
    return epoch


def source_context_mutation(service):
    """Short registry mutation fence, no provider I/O or Storage.

    Historical service facades have no asynchronous purchase authority.
    Keep their epoch-only behavior unchanged.
    """
    lock = getattr(service, "_source_context_lock", None)
    return lock if lock is not None else nullcontext()


def revoke_source_context(service):
    with source_context_mutation(service):
        service._source_context_epoch = source_context_epoch(service) + 1
