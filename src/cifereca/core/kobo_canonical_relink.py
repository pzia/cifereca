"""#4859 Core-B: relink an already published Kobo B without legacy W4 writers.

A new local pathname is NOT a right. Only a pre-existing exact provider
SourceReference + completed OriginLink + Artifact + missing canonical Placement
can authorize moving that *same* Placement pointer after fresh file validation.
No copy, GET, ADEPT, backup_items, legacy ledger or entitlement changes.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import stat
from typing import Callable

from ..locking import InterProcessLock, LockBusyError
from ..paths import acquisition_lock_file
from ..plugin_api.provider_rights_observation import ProviderRightsScope
from .canonical_publication import _stable, _same_file
from .canonical_legacy_adoption import _directory_signature
from .canonical_recovery import _exact_borrower_bound_in_db
from .convergence_verified_presence import _verified_copy

_MAX_PROFILES = 10000
_SCHEMA = "core.kobo.canonical-placement-relink/1"
_JOURNAL = "core.kobo.canonical-placement-relink."


class CanonicalRelinkRejected(ValueError):
    """Stable non-sensitive refusal; never surface SQLite or path errors."""

    _KNOWN = frozenset({
        "invalid_relink_request", "canonical_relink_scope_unavailable",
        "canonical_relink_right_unverified", "canonical_relink_origin_unverified",
        "canonical_relink_placement_unavailable",
        "canonical_relink_file_unverified", "canonical_relink_old_path_present",
        "canonical_relink_destination_claimed",
        "canonical_relink_plan_changed", "canonical_relink_busy",
        "canonical_relink_unavailable",
    })

    def __init__(self, code: str):
        self.code = code if code in self._KNOWN else "canonical_relink_unavailable"
        super().__init__(self.code)


def _require(ok, code):
    if not ok:
        raise CanonicalRelinkRejected(code)


def _id(value, *, limit=256):
    return (type(value) is str and 0 < len(value) <= limit
            and value == value.strip()
            and all(ord(ch) >= 32 and ord(ch) != 127 for ch in value))


def _inspect(db, *, library_root, scope, edition_id, right_id,
             branch_id, placement_id, new_path):
    _require(
        _exact_borrower_bound_in_db(db, scope),
        "canonical_relink_scope_unavailable",
    )
    identities = db.execute(
        "SELECT identity_id,directory_name FROM identities LIMIT ?",
        (_MAX_PROFILES + 1,),
    ).fetchall()
    _require(len(identities) <= _MAX_PROFILES, "canonical_relink_scope_unavailable")
    directories = [folder for owner, folder in identities if owner == scope.identity_id]
    _require(
        len(directories) == 1 and _id(directories[0])
        and sum(folder == directories[0] for _, folder in identities) == 1,
        "canonical_relink_scope_unavailable",
    )
    root = library_root.resolve(strict=True)
    owner_dir = root / directories[0]
    signatures = (_directory_signature(root), _directory_signature(owner_dir))
    _require(all(x is not None for x in signatures), "canonical_relink_file_unverified")
    source_ref = _stable(
        "source", scope.identity_id, scope.provider_id, scope.credential_id,
        scope.account_id, branch_id, edition_id, right_id, "purchase",
    )
    source = db.execute(
        "SELECT state,evidence_kind,evidence_ref,identity_id,provider_id,"
        "credential_id,account_id,branch_id,edition_id,right_id,reference_kind "
        "FROM source_references WHERE source_reference_id=?",
        (source_ref,),
    ).fetchone()
    _require(
        source is not None
        and source[:2] == ("active", "provider_response")
        and type(source[2]) is str and bool(source[2])
        and source[3:] == (
            scope.identity_id, "kobo", scope.credential_id, scope.account_id,
            branch_id, edition_id, right_id, "purchase",
        ),
        "canonical_relink_right_unverified",
    )
    placement = db.execute(
        "SELECT path,identity_id,artifact_id,provider_id,source_item_id,state "
        "FROM placements WHERE placement_id=?",
        (placement_id,),
    ).fetchone()
    _require(
        placement is not None
        and placement[1] == scope.identity_id
        and placement[3:5] == (None, None)
        and placement[5] in {"present", "missing"}
        and type(placement[0]) is str
        and type(placement[2]) is str
        and placement[2].startswith("sha256:"),
        "canonical_relink_placement_unavailable",
    )
    artifact = db.execute(
        "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
        (placement[2],),
    ).fetchone()
    _require(
        artifact is not None and type(artifact[0]) is str
        and len(artifact[0]) == 64
        and all(c in "0123456789abcdef" for c in artifact[0])
        and placement[2] == "sha256:" + artifact[0]
        and type(artifact[1]) is int and artifact[1] > 0,
        "canonical_relink_origin_unverified",
    )
    origin = db.execute(
        "SELECT origin_link_id,evidence_ref FROM origin_links "
        "WHERE origin_kind='provider' AND identity_id=? "
        "AND artifact_id=? AND source_reference_id=? LIMIT 2",
        (scope.identity_id, placement[2], source_ref),
    ).fetchall()
    _require(
        len(origin) == 1 and type(origin[0][1]) is str
        and bool(origin[0][1]),
        "canonical_relink_origin_unverified",
    )
    # An old canonical Placement without bytes is a locator to repair, not a
    # right to create another copy. Never mutate while its old pathname still
    # exists (including an unsafe symlink), even if the new file has same SHA.
    old_path = placement[0]
    old_file = Path(old_path)
    _require(
        old_file.is_absolute()
        and ".." not in old_file.parts
        and old_file != new_path
        and old_file.is_relative_to(owner_dir)
        and not os.path.lexists(old_path),
        "canonical_relink_old_path_present",
    )
    existing = db.execute(
        "SELECT placement_id FROM placements WHERE path=? LIMIT 2",
        (str(new_path),),
    ).fetchall()
    _require(not existing, "canonical_relink_destination_claimed")
    # Canonical publishers store physically resolved paths. A symlinked
    # library alias must not create a second SQL pathname for the same B.
    try:
        _require(new_path.resolve(strict=True) == new_path,
                 "canonical_relink_file_unverified")
    except (OSError, RuntimeError):
        raise CanonicalRelinkRejected("canonical_relink_file_unverified") from None
    _require(
        _verified_copy(
            str(new_path), root=library_root,
            owner_directory=directories[0],
            registered_directories=frozenset(
                x for _, x in identities if type(x) is str
            ),
            size=artifact[1], sha256=artifact[0],
        ),
        "canonical_relink_file_unverified",
    )
    try:
        before = os.stat(new_path, follow_symlinks=False)
    except OSError:
        raise CanonicalRelinkRejected("canonical_relink_file_unverified") from None
    _require(
        stat.S_ISREG(before.st_mode) and _same_file(new_path, before)
        and signatures == (
            _directory_signature(root), _directory_signature(owner_dir),
        ),
        "canonical_relink_file_unverified",
    )
    stamp = (
        old_path, placement[1], placement[2], placement[3],
        placement[4], placement[5], artifact, source,
        origin[0], signatures,
        tuple(getattr(before, key) for key in (
            "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns",
        )),
    )
    plan = {
        "schema": _SCHEMA,
        "scope": [scope.identity_id, "kobo", scope.credential_id, scope.account_id,
                  branch_id, edition_id, right_id],
        "placement_id": placement_id,
        "old_path": old_path, "new_path": str(new_path),
        "evidence": stamp,
    }
    token = hashlib.sha256(
        json.dumps(plan, ensure_ascii=True, separators=(",", ":"), default=list)
        .encode("utf-8"),
    ).hexdigest()
    return (
        token, old_path, before, signatures, directories[0],
        source_ref, artifact[0], source[2], origin[0][1],
    )


def relink_verified_canonical_kobo(
    storage, *, library_root: Path, identity_id: str, credential_id: str,
    account_id: str, edition_id: str, right_id: str, branch_id: str,
    placement_id: str, new_path: Path, confirmation: str | None = None,
    context_current: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Preview/relink only an EXISTING provider B, never infer fresh ownership.

    A confirm token is tied to SQL rows, account, old/new paths and current
    inode/mtime. The writer also checks the profile and physical file again
    directly before commit. On success, only one Placement pathname, its
    last_seen timestamp and an immutable audit journal change.
    """
    _require(
        all(_id(x) for x in (
            identity_id, credential_id, account_id, edition_id,
            right_id, branch_id, placement_id,
        ))
        and isinstance(library_root, Path)
        and library_root.is_absolute()
        and isinstance(new_path, Path) and new_path.is_absolute()
        and ".." not in new_path.parts
        and (confirmation is None or (
            type(confirmation) is str and len(confirmation) == 64
            and all(c in "0123456789abcdef" for c in confirmation)
        ))
        and (context_current is None or callable(context_current)),
        "invalid_relink_request",
    )
    scope = ProviderRightsScope(identity_id, "kobo", credential_id, account_id)
    write = confirmation is not None
    try:
        guard = InterProcessLock(acquisition_lock_file()) if write else __import__("contextlib").nullcontext()
        with guard, storage._lock, storage._connect() as db:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            _require(
                context_current is None or context_current() is True,
                "canonical_relink_scope_unavailable",
            )
            (token, old_path, observed, signatures,
             owner_folder, source_ref, artifact_sha,
             source_evidence, origin_evidence) = _inspect(
                db, library_root=library_root, scope=scope,
                edition_id=edition_id, right_id=right_id,
                branch_id=branch_id, placement_id=placement_id,
                new_path=new_path,
            )
            if not write:
                return {
                    "schema": _SCHEMA, "status": "preview", "ok": True,
                    "changed": 0, "plan_token": token,
                    "legacy_backup_mutations": 0, "rights_mutations": 0,
                    "file_mutations": 0,
                }
            _require(confirmation == token, "canonical_relink_plan_changed")
            # Strictly one canonical Placement. No legacy projection, right
            # or OriginLink may be retroactively created by a physical relink.
            now = int(time.time())
            changed = db.execute(
                "UPDATE placements SET path=?,state='present',"
                "last_seen_at=?,updated_at=? WHERE placement_id=? "
                "AND path=? AND identity_id=? AND artifact_id=? "
                "AND state IN('present','missing') "
                "AND provider_id IS NULL AND source_item_id IS NULL",
                (str(new_path), now, now, placement_id, old_path,
                 identity_id, "sha256:" + artifact_sha),
            )
            _require(changed.rowcount == 1, "canonical_relink_plan_changed")
            key = _JOURNAL + hashlib.sha256(
                "\x1f".join((placement_id, old_path, str(new_path)))
                .encode("utf-8"),
            ).hexdigest()
            db.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?)",
                (key, json.dumps({
                    "schema": _SCHEMA, "source_reference_id": source_ref,
                    "identity_id": identity_id, "placement_id": placement_id,
                    "old_path": old_path, "new_path": str(new_path),
                    "verified_file_size": observed.st_size,
                    "verified_file_sha256": artifact_sha,
                    "confirmed_at": now,
                }, ensure_ascii=True, sort_keys=True)),
            )
            # SQL triggers or injected maintenance must not transform a
            # canonical origin into an unrelated claim inside this transaction.
            source_now = db.execute(
                "SELECT state,evidence_kind,evidence_ref FROM source_references "
                "WHERE source_reference_id=? AND identity_id=? "
                "AND provider_id='kobo' AND credential_id=? AND account_id=? "
                "AND branch_id=? AND edition_id=? AND right_id=? "
                "AND reference_kind='purchase'",
                (source_ref, identity_id, credential_id, account_id,
                 branch_id, edition_id, right_id),
            ).fetchall()
            origin_now = db.execute(
                "SELECT evidence_ref FROM origin_links WHERE origin_kind='provider' "
                "AND identity_id=? AND artifact_id=? AND source_reference_id=? "
                "AND typeof(evidence_ref)='text' AND evidence_ref!='' LIMIT 2",
                (identity_id, "sha256:" + artifact_sha, source_ref),
            ).fetchall()
            _require(
                source_now == [("active", "provider_response", source_evidence)]
                and origin_now == [(origin_evidence,)]
                and db.execute(
                    "SELECT directory_name FROM identities WHERE identity_id=?",
                    (identity_id,),
                ).fetchall() == [(owner_folder,)]
                and _exact_borrower_bound_in_db(db, scope)
                and _same_file(new_path, observed)
                and (_directory_signature(library_root.resolve(strict=True)),
                     _directory_signature(library_root.resolve(strict=True) / owner_folder)) == signatures
                and not os.path.lexists(old_path),
                "canonical_relink_file_unverified",
            )
            _require(
                context_current is None or context_current() is True,
                "canonical_relink_plan_changed",
            )
            return {
                "schema": _SCHEMA, "status": "relinked", "ok": True,
                "changed": 1, "plan_token": None,
                "legacy_backup_mutations": 0, "rights_mutations": 0,
                "file_mutations": 0,
            }
    except LockBusyError:
        raise CanonicalRelinkRejected("canonical_relink_busy") from None
    except CanonicalRelinkRejected:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, RuntimeError):
        raise CanonicalRelinkRejected("canonical_relink_unavailable") from None


__all__ = ["CanonicalRelinkRejected", "relink_verified_canonical_kobo"]
