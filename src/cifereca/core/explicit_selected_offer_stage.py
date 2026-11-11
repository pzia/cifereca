"""One exact Core completed-delivery stage, checked inside the publisher SQL txn.

Not permission to download, not a purchase right and not a historical
manifest. This verifies only that Core already staged THIS selected supplier
offer and THESE completed bytes under the same source scope. The publisher
must separately check the live Core lease, user consent, enabled connection,
exact borrower, file/inode/hash, and final OriginLink transaction.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .canonical_recovery import _SCHEMA, _STAGED, _scope, _stage_id
from .source_convergence import SourceKey


def selected_offer_stage_current_in_transaction(
    db, source: SourceKey, *, offer_id: str, path: Path,
    final_sha256: str, delivery_receipt_ref: str,
    adept_receipt_ref: str = "",
) -> bool:
    """Read-only exact stage check; fail closed on wrong/corrupt/replayed row.

    Caller MUST already hold Core's exclusive acquisition lock and a SQLite
    BEGIN IMMEDIATE. The stage was issued only after final bytes validation,
    but the caller remains responsible for rehashing the physical final file.
    """
    if (type(source) is not SourceKey
            or type(offer_id) is not str or not offer_id or len(offer_id) > 256
            or not isinstance(path, Path) or not path.is_absolute()
            or type(final_sha256) is not str or len(final_sha256) != 64
            or any(c not in "0123456789abcdef" for c in final_sha256)
            or type(delivery_receipt_ref) is not str or not delivery_receipt_ref
            or len(delivery_receipt_ref) > 1024
            or type(adept_receipt_ref) is not str
            or len(adept_receipt_ref) > 1024
            or any("\x00" in value or "\r" in value or "\n" in value for value in (
                offer_id, delivery_receipt_ref, adept_receipt_ref,
            ))):
        return False
    try:
        scope = source.scope
        stage_id = _stage_id(source, offer_id)
        rows = db.execute(
            "SELECT identity_id,provider_id,credential_id,account_id,item_id,"
            "status,artifact_id,metadata_json FROM acquisition_ledger "
            "WHERE acquisition_id=? LIMIT 2",
            (stage_id,),
        ).fetchall()
        if len(rows) != 1:
            return False
        identity, provider, credential, account, edition, status, artifact, raw = rows[0]
        if ((identity, provider, credential, account, edition)
                != (scope.identity_id, scope.provider_id,
                    scope.credential_id, scope.account_id, source.edition_id)
                or status != _STAGED or artifact is not None
                or type(raw) is not str or len(raw) > 16384):
            return False
        # Reject duplicate keys: a JSON object's last duplicate value must
        # not override the first attested source/offer coordinate.
        from .canonical_recovery import _unique_checkpoint_fields
        stage = json.loads(raw, object_pairs_hook=_unique_checkpoint_fields)
        return (
            type(stage) is dict
            and stage.get("schema") == _SCHEMA
            and stage.get("source") == _scope(source)
            and stage.get("offer_id") == offer_id
            and stage.get("path") == str(path)
            and stage.get("sha256") == final_sha256
            and stage.get("receipt") == delivery_receipt_ref
            and stage.get("adept_receipt") == adept_receipt_ref
        )
    except (AttributeError, TypeError, ValueError, sqlite3.Error):
        return False
