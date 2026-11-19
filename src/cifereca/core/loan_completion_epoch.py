"""#5225: a durable negative source fence inside the loan receipt transaction.

No provider, account grant, filesystem or network authority follows from it.
Use the existing epoch table rather than a second generation or writer.
"""
from __future__ import annotations

from .loan_source_epoch import SourceEpochLease


class CompletionEpochRevoked(ValueError):
    """The completed receipt cannot commit under the pinned source."""


def _text(value, *, empty=False):
    return (type(value) is str and (empty or bool(value)) and len(value) <= 1024
            and all(32 <= ord(c) != 127 for c in value))


def validate_completion_receipt(row, lease):
    """Require exact native receipt coordinates before taking the SQL lock."""
    if (type(lease) is not SourceEpochLease
            or not all(_text(value) for value in (
                lease.provider_id, lease.credential_id, lease.identity_id, lease.loan_id))
            or (lease.account_id is not None and not _text(lease.account_id, empty=True))
            or (lease.record_id is not None and not _text(lease.record_id, empty=True))
            or type(lease.epoch) is not int or lease.epoch < 1
            or type(row) is not dict):
        raise CompletionEpochRevoked("loan_completion_source_changed")
    expected = {
        "provider_id": lease.provider_id, "credential_id": lease.credential_id,
        "identity_id": lease.identity_id, "account_id": lease.account_id,
        "item_id": lease.record_id or lease.loan_id,
        "status": "complete", "strategy": "loan_auto",
    }
    if (any(type(row.get(key)) is not type(value) or row.get(key) != value
            for key, value in expected.items())
            or type(row.get("metadata")) is not dict
            or type(row["metadata"].get("provider_loan_id")) is not str
            or row["metadata"]["provider_loan_id"] != lease.loan_id):
        raise CompletionEpochRevoked("loan_completion_source_changed")


def require_completion_epoch(db, lease):
    """Called only under the writer's BEGIN IMMEDIATE, before and after INSERT."""
    if not db.in_transaction:
        raise CompletionEpochRevoked("loan_completion_source_changed")
    rows = db.execute(
        "SELECT c.identity_id,c.enabled,e.epoch FROM provider_connections AS c "
        "JOIN provider_connection_epochs AS e ON e.provider_id=c.provider_id "
        "AND e.credential_id=c.credential_id WHERE c.provider_id=? "
        "AND c.credential_id=? LIMIT 2",
        (lease.provider_id.casefold(), lease.credential_id),
    ).fetchall()
    if rows != [(lease.identity_id, 1, lease.epoch)]:
        raise CompletionEpochRevoked("loan_completion_source_changed")
