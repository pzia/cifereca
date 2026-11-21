"""Local ADEPT evidence inventory (#5843), not a readiness/rights resolver.

No Core startup, Config, provider, ADEPT runtime or user-content inspection.
Query one coherent private SQLite image in memory, never a source SQLite handle. Report only
closed states, counts and report-local ordinals, never persisted coordinates.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import json
import os
import sqlite3
import stat
import time

from ..paths import database_file
from ..storage import CURRENT_SCHEMA_VERSION, Storage
from .library_inventory import _fingerprint, _open_root
from .canonical_recovery import _unique_checkpoint_fields

_LIMIT = 100_000
_MAX_IMAGE = 128 * 1024 * 1024
_SECONDS = 10


class _Incomplete(Exception):
    pass


def failed_report(code):
    return {"schema": 1, "status": "unavailable", "code": code,
            "read_only": True, "runtime_checked": False,
            "provider_checked": False, "authorization_attested": False}


def _coordinate(value, *, empty=False):
    return (type(value) is str and (empty or bool(value))
            and len(value) <= 1024 and value == value.strip()
            and all(32 <= ord(c) != 127 for c in value))


def _rows(db, sql):
    rows = db.execute(sql + " LIMIT ?", (_LIMIT + 1,)).fetchall()
    if len(rows) > _LIMIT:
        raise _Incomplete("row_budget_exceeded")
    return rows


def _snapshot(db, *, identity, provider, credential, account_id):
    identities = {r[0] for r in _rows(db, "SELECT identity_id FROM identities")}
    bindings = defaultdict(list)
    for scope, owner, profile in _rows(db,
            "SELECT scope_type,scope_id,profile_id FROM adept_profile_bindings"):
        if scope == "identity":
            bindings[owner].append(profile)
    profile_owners = Counter(p for values in bindings.values() for p in values)

    def binding_state(owner):
        profiles = bindings.get(owner, [])
        if owner not in identities:
            return "identity_missing"
        if not profiles:
            return "binding_missing"
        if len(profiles) != 1 or profile_owners[profiles[0]] != 1:
            return "binding_ambiguous"
        if not _coordinate(profiles[0]):
            return "binding_invalid"
        return "binding_recorded"

    connections = defaultdict(list)
    for row in _rows(db, "SELECT identity_id,provider_id,credential_id,"
                          "connection_id,enabled FROM provider_connections"):
        connections[row[:3]].append(row[3:])
    accounts = defaultdict(list)
    for row in _rows(db, "SELECT identity_id,provider_id,credential_id,account_id,"
                          "connection_id,is_primary FROM provider_accounts"):
        accounts[row[:4]].append(row[4:])

    grants = defaultdict(list)
    for row in _rows(db, "SELECT provider_id,credential_id,account_id,"
                          "connection_identity_id,borrower_identity_id,profile_id,"
                          "confirmed_by FROM adept_borrower_grants"):
        grants[row[:3]].append(row[3:])
    generations = dict((r[:3], r[3]) for r in _rows(db,
        "SELECT provider_id,credential_id,account_id,generation "
        "FROM adept_borrower_grant_generations"))
    loans = defaultdict(list)
    for row in _rows(db, "SELECT identity_id,provider_id,credential_id,account_id,"
                          "source_reference_id,state,edition_id,right_id FROM source_references "
                          "WHERE reference_kind='loan'"):
        loans[row[:4]].append(row[4:])
    # Historical ledgers have no branch coordinate: a match is only a
    # candidate observation if the edition/right pair is unique in its scope.
    # Never treat historical metadata as canonical provenance or activation.
    rights = defaultdict(list)
    for row in _rows(db, "SELECT identity_id,provider_id,credential_id,account_id,"
                          "item_id,loan_id,state FROM entitlements "
                          "WHERE entitlement_type='loan'"):
        rights[row[:6]].append(row[6])
    historical = defaultdict(list)
    for row in _rows(db, "SELECT identity_id,provider_id,credential_id,account_id,"
                          "item_id,status,executor,"
                          "CASE WHEN length(CAST(metadata_json AS BLOB))<=65536 "
                          "THEN metadata_json END FROM acquisition_ledger "
                          "WHERE strategy='loan_auto'"):
        historical[row[:5]].append(row[5:])
    origins = defaultdict(list)
    for ref, owner, receipt in _rows(db,
            "SELECT source_reference_id,identity_id,"
            "CASE WHEN length(evidence_ref)>0 THEN 1 ELSE 0 END "
            "FROM origin_links WHERE origin_kind='adept'"):
        origins[ref].append((owner, receipt))

    def historical_states(scope, refs):
        pairs = Counter((edition, right) for _, _, edition, right in refs)
        ledger_counts = Counter()
        right_counts = Counter()
        binding_counts = Counter()
        # Parse each edition's historical records once per selected account;
        # malformed data cannot prove that a matching loan is absent.
        grouped = {}
        for _, local_state, edition, right in refs:
            if pairs[(edition, right)] != 1:
                ledger_counts["ambiguous_reference"] += 1
                right_counts["ambiguous_reference"] += 1
                continue
            observed = rights.get((*scope, edition, right), [])
            if not observed:
                right_counts["not_recorded"] += 1
            elif len(observed) != 1:
                right_counts["ambiguous"] += 1
            else:
                state = observed[0]
                right_counts["same_local_state" if state == local_state
                             else "different_local_state"] += 1
            if edition not in grouped:
                rows = historical.get((*scope, edition), [])
                by_loan = defaultdict(list)
                valid = len(rows) <= 256
                if valid:
                    for status, executor, raw in rows:
                        try:
                            metadata = json.loads(raw, object_pairs_hook=_unique_checkpoint_fields)
                        except (ValueError, TypeError, RecursionError):
                            valid = False
                            break
                        if type(metadata) is not dict or not _coordinate(metadata.get("provider_loan_id")):
                            valid = False
                            break
                        by_loan[metadata["provider_loan_id"]].append(
                            (status, executor, metadata.get("adept_binding")))
                grouped[edition] = by_loan if valid else None
            by_loan = grouped[edition]
            if by_loan is None:
                ledger_counts["unverified"] += 1
                continue
            matches = by_loan.get(right, [])
            if not matches:
                ledger_counts["not_recorded"] += 1
                continue
            if len(matches) != 1:
                ledger_counts["ambiguous_transaction"] += 1
                continue
            status, executor, binding = matches[0]
            if status != "complete" or executor != "kobo-adept":
                ledger_counts["historical_attempt_or_non_adept"] += 1
                continue
            if (type(binding) is not dict
                    or not _coordinate(binding.get("adept_profile_id"))
                    or not _coordinate(binding.get("adept_loan_id"))):
                ledger_counts["binding_incomplete"] += 1
                continue
            ledger_counts["recorded"] += 1
            profile = binding["adept_profile_id"]
            # This compares references only. A current grant is not proof
            # of the historical transaction's activation or its Adobe owner.
            key = scope[1:]
            candidates = grants.get(key, [])
            if len(candidates) == 1 and candidates[0][0] == scope[0]:
                candidate = candidates[0]
                borrower, pinned = candidate[1:3]
                current = (pinned if binding_state(borrower) == "binding_recorded"
                           and bindings.get(borrower) == [pinned] else None)
            else:
                current = None
            if current is None:
                binding_counts["current_explicit_grant_unverified"] += 1
            else:
                binding_counts["same_profile_reference" if profile == current
                               else "different_profile_reference"] += 1
        return {"legacy_loan_right_records": dict(sorted(right_counts.items())),
                "historical_transaction_records": dict(sorted(ledger_counts.items())),
                "historical_grant_profile_comparison": dict(sorted(binding_counts.items()))}

    def selected(scope):
        return all(filter_value is None or value == filter_value
                   for value, filter_value in zip(
                       scope, (identity, provider, credential, account_id)))

    # Only persisted account rows can supply an account filter, not an
    # OriginLink, a grant, a primary label or a selected GUI connection.
    account_scopes = defaultdict(list)
    for scope in accounts:
        account_scopes[scope[1:]].append(scope)
    selected_accounts = [scope for scope in accounts if selected(scope)]
    selected_accounts.sort(key=lambda s: tuple(str(v) for v in s))
    records = []
    counts = {"accounts": len(selected_accounts), "connections_without_account": 0,
              "loan_references_without_account": 0, "orphan_identity_bindings": 0,
              "identities_without_binding": 0, "grant_rows_without_account": 0}
    for index, scope in enumerate(selected_accounts, 1):
        owner, source, cred, account = scope
        conn = connections[scope[:3]]
        accts = accounts[scope]
        valid_scope = all(_coordinate(v, empty=(i == 3))
                          for i, v in enumerate(scope))
        coherent = (valid_scope and owner in identities and len(conn) == 1
                    and len(accts) == 1 and conn[0][0] == accts[0][0]
                    and _coordinate(conn[0][0]) and conn[0][1] == 1)
        connection_state = "connection_recorded" if coherent else "connection_unverified"
        if len(conn) == len(accts) == 1 and conn[0][1] == 0:
            connection_state = "connection_disabled"
        key = (source, cred, account)
        candidates = grants[key]
        generation = generations.get(key)
        source_accounts = account_scopes[key]
        if not candidates:
            grant = ("grant_absent_generation_recorded" if generation is not None
                     else "grant_not_recorded")
        elif len(candidates) != 1 or len(source_accounts) != 1:
            grant = "grant_ambiguous"
        else:
            connection_owner, borrower, profile, confirmation = candidates[0]
            valid = (coherent and bool(account) and connection_owner == owner
                     and binding_state(borrower) == "binding_recorded"
                     and bindings.get(borrower) == [profile] and confirmation == "explicit"
                     and type(generation) is int and 0 < generation < 9223372036854775807)
            grant = "explicit_grant_recorded" if valid else "grant_unverified"
        refs = loans[scope]
        linked = [origin for ref, _state, _edition, _right in refs for origin in origins[ref]]
        records.append({
            "account": index, "connection": connection_state,
            "identity_binding": binding_state(owner), "borrower_grant": grant,
            "loan_references": len(refs), "adept_origin_records": len(linked),
            "origin_owner_mismatch": sum(1 for o, _ in linked if o != owner),
            "origin_receipt_missing": sum(1 for _, receipt in linked if not receipt),
            "origin_multiple_records": sum(len(origins[ref]) > 1 for ref, _state, _edition, _right in refs),
            "loan_references_without_adept_origin": sum(not origins[ref] for ref, _state, _edition, _right in refs),
            "loan_reference_states": {
                label: sum(state == label for _ref, state, _edition, _right in refs)
                for label in ("active", "inactive", "unknown")
            },
            **historical_states(scope, refs),
            "provisioning": "unknown", "current_right": "unknown",
        })
    if account_id is None:
        account_connections = {s[:3] for s in accounts}
        counts["connections_without_account"] = sum(
            selected(s + (None,)) and s not in account_connections for s in connections)
        counts["loan_references_without_account"] = sum(
            len(refs) for s, refs in loans.items() if selected(s) and s not in accounts)
        # Grant ownership is an explicit coordinate, not inferred from profile.
        counts["grant_rows_without_account"] = sum(
            selected((g[0], *key)) and (g[0], *key) not in accounts
            for key, values in grants.items() for g in values)
    profile_records = None
    if provider is None and credential is None and account_id is None:
        selected_owners = {owner for owner in set(bindings) | identities
                           if identity is None or owner == identity}
        profile_records = dict(sorted(Counter(binding_state(owner)
                                               for owner in selected_owners).items()))
        counts["orphan_identity_bindings"] = sum(
            owner not in identities and (identity is None or owner == identity)
            for owner in bindings)
        counts["identities_without_binding"] = sum(
            not bindings.get(owner) and (identity is None or owner == identity)
            for owner in identities)
    return {"schema": 1, "status": "complete", "read_only": True,
            "runtime_checked": False, "provider_checked": False,
            "authorization_attested": False, "snapshot": "sqlite_only",
            "scope": "all" if all(v is None for v in
                (identity, provider, credential, account_id)) else "filtered",
            "counts": counts, "accounts": records,
            "recorded_identity_profile_states": profile_records,
            "profile_inventory": "unknown", "provisioning": "unknown",
            "current_rights": "unknown"}


def _private_image(path, deadline):
    """Stable direct image or existing isolated read-only WAL capture.

    mode=ro SQLite can create or update a source SHM. Never use it here.
    Do not read immutable=1 against a live database or ignore a journal.
    """
    path = path.absolute()
    if ".." in path.parts:
        raise _Incomplete("local_evidence_unavailable")
    parent = fd = -1
    live = False
    try:
        parent = _open_root(path.parent)

        def present(suffix):
            try:
                os.stat(path.name + suffix, dir_fd=parent, follow_symlinks=False)
                return True
            except FileNotFoundError:
                return False

        if present("-journal"):
            raise _Incomplete("snapshot_live_journal")
        live = present("-wal")
        if not live:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                         | os.O_CLOEXEC, dir_fd=parent)
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or not 100 <= before.st_size <= _MAX_IMAGE:
                raise _Incomplete("snapshot_byte_budget_or_invalid")
            parts, remaining = [], before.st_size
            while remaining:
                if time.monotonic() >= deadline:
                    raise _Incomplete("snapshot_time_budget")
                chunk = os.read(fd, min(1024 * 1024, remaining))
                if not chunk:
                    raise _Incomplete("snapshot_changed")
                parts.append(chunk)
                remaining -= len(chunk)
            if (present("-wal") or present("-journal")
                    or _fingerprint(before) != _fingerprint(os.fstat(fd))
                    or _fingerprint(before) != _fingerprint(
                        os.stat(path.name, dir_fd=parent, follow_symlinks=False))):
                raise _Incomplete("snapshot_changed")
            current = _open_root(path.parent)
            try:
                old, new = os.fstat(parent), os.fstat(current)
                if (old.st_dev, old.st_ino) != (new.st_dev, new.st_ino):
                    raise _Incomplete("snapshot_changed")
            finally:
                os.close(current)
            return b"".join(parts)
    finally:
        if fd >= 0:
            os.close(fd)
        if parent >= 0:
            os.close(parent)
    # Reuse the already-qualified isolated local reader. Its child uses
    # shared locks without SQLite source handles; it never contacts ADEPT.
    from .library_wal_image import read_wal_image
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _Incomplete("snapshot_time_budget")
    image, reason = read_wal_image(path, max_bytes=_MAX_IMAGE, seconds=remaining)
    if reason or image is None:
        raise _Incomplete("snapshot_live_unavailable")
    return image


def diagnose_local_adept(*, identity=None, provider=None, credential=None,
                         account_id=None, database_path=None):
    """No schema creation/migration. An incomplete snapshot is never success."""
    filters = (identity, provider, credential, account_id)
    if any(v is not None and not _coordinate(v, empty=(i == 3))
           for i, v in enumerate(filters)):
        return failed_report("invalid_scope")
    db = None
    try:
        path = Path(database_path) if database_path is not None else database_file(create=False)
        deadline = time.monotonic() + _SECONDS
        image = _private_image(path, deadline)
        if (not 100 <= len(image) <= _MAX_IMAGE
                or image[:16] != b"SQLite format 3\0"):
            raise _Incomplete("local_evidence_unavailable")
        db = sqlite3.connect(":memory:")
        if not hasattr(db, "deserialize"):
            raise _Incomplete("snapshot_reader_unavailable")
        # Normalize only the private copy; a WAL header otherwise seeks sidecars.
        db.deserialize(image[:18] + b"\x01\x01" + image[20:])
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        db.execute("BEGIN")
        if Storage._read_schema_version(db) != CURRENT_SCHEMA_VERSION:
            return failed_report("schema_unsupported")
        return _snapshot(db, identity=identity, provider=provider,
                         credential=credential, account_id=account_id)
    except _Incomplete as exc:
        return failed_report(exc.args[0])
    except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError, AttributeError):
        return failed_report("local_evidence_unavailable")
    finally:
        if db is not None:
            db.close()
