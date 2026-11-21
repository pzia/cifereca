"""Local ADEPT evidence inventory (#5843), not a readiness/rights resolver.

No Core, Config, provider, runtime, subprocess or file-content inspection.
Read the existing database in a single query-only snapshot. Report only
closed states, counts and report-local ordinals, never persisted coordinates.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import sqlite3

from ..paths import database_file
from ..storage import CURRENT_SCHEMA_VERSION, Storage

_LIMIT = 100_000


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
                          "source_reference_id,state FROM source_references "
                          "WHERE reference_kind='loan'"):
        loans[row[:4]].append(row[4])
    origins = defaultdict(list)
    for ref, owner, receipt in _rows(db,
            "SELECT source_reference_id,identity_id,"
            "CASE WHEN length(evidence_ref)>0 THEN 1 ELSE 0 END "
            "FROM origin_links WHERE origin_kind='adept'"):
        origins[ref].append((owner, receipt))

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
        linked = [origin for ref, _state in refs for origin in origins[ref]]
        records.append({
            "account": index, "connection": connection_state,
            "identity_binding": binding_state(owner), "borrower_grant": grant,
            "loan_references": len(refs), "adept_origin_records": len(linked),
            "origin_owner_mismatch": sum(1 for o, _ in linked if o != owner),
            "origin_receipt_missing": sum(1 for _, receipt in linked if not receipt),
            "origin_multiple_records": sum(len(origins[ref]) > 1 for ref, _state in refs),
            "loan_references_without_adept_origin": sum(not origins[ref] for ref, _state in refs),
            "loan_reference_states": {
                label: sum(state == label for _ref, state in refs)
                for label in ("active", "inactive", "unknown")
            },
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
    if provider is None and credential is None and account_id is None:
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
            "profile_inventory": "unknown", "provisioning": "unknown",
            "current_rights": "unknown"}


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
        db = sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True, timeout=2)
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        if Storage._read_schema_version(db) != CURRENT_SCHEMA_VERSION:
            return failed_report("schema_unsupported")
        return _snapshot(db, identity=identity, provider=provider,
                         credential=credential, account_id=account_id)
    except _Incomplete as exc:
        return failed_report(exc.args[0])
    except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError):
        return failed_report("local_evidence_unavailable")
    finally:
        if db is not None:
            db.close()
