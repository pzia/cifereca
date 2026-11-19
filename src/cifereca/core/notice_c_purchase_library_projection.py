"""Presentation-only purchase descriptions for exact remote Library cards."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import sqlite3

from ..plugin_api.downloadable_resources import SourceReference
from ..plugin_api.provider_rights_observation import ProviderRightsScope
from .notice_c_purchase_descriptive_read import PurchaseDescriptiveNotice, _read_descriptive_txn

_MAX_ROWS = 10_000
_PACKET = "_purchase_library_notice"


@dataclass(frozen=True, slots=True, repr=False)
class PurchaseLibraryNotice:
    key: str
    source: SourceReference
    notice: PurchaseDescriptiveNotice
    placement: tuple[str, str] | None = None


def _coordinate(row, identity_id):
    if (type(row) is not dict or row.get("identity_id") != identity_id
            or row.get("_library_view_owner") not in (None, identity_id)
            or row.get("source") != "private_catalog_observation"
            or row.get("state") != "remote_catalog" or row.get("local") is not False
            or row.get("artifact_id") is not None or row.get("placement_id") is not None
            or type(row.get("key")) is not str or not row["key"]):
        return None
    sources = row.get("source_refs")
    if type(sources) is not list or len(sources) != 1 or type(sources[0]) is not dict:
        return None
    source = sources[0]
    # Offline observation is not promoted into an action or a right.
    if (source.get("kind") not in ("purchase", "observation")
            or source.get("identity_id") != identity_id
            or row.get("provider_id") != source.get("provider_id")
            or row.get("item_id") != source.get("item_id")):
        return None
    coordinate = tuple(source.get(name) for name in (
        "provider_id", "credential_id", "account_id", "item_id",
    ))
    if not all(type(value) is str and 0 < len(value) <= 1024
               and not any(ch in value for ch in "\x00\r\n") for value in coordinate):
        return None
    return coordinate


def _local_coordinate(row, identity_id):
    if (type(row) is not dict or row.get("identity_id") != identity_id
            or row.get("_library_view_owner") not in (None, identity_id)
            or row.get("local") is not True or row.get("state") != "local"):
        return None
    pair = (row.get("artifact_id"), row.get("placement_id"))
    return pair if all(type(v) is str and v for v in pair) else None


def purchase_library_notice(row):
    """GTK-safe consistency check; only worker-created native packets."""
    if type(row) is not dict:
        return None
    packet = row.get(_PACKET)
    if type(packet) is not PurchaseLibraryNotice or type(packet.source) is not SourceReference:
        return None
    source = packet.source
    if (type(packet.notice) is not PurchaseDescriptiveNotice
            or row.get("key") != packet.key or source.kind != "purchase"
             ):
        return None
    if packet.placement is not None:
        return packet if _local_coordinate(row, source.scope.identity_id) == packet.placement else None
    if _coordinate(row, source.scope.identity_id) != (
                source.scope.provider_id, source.scope.credential_id,
                source.scope.account_id, source.edition_id):
        return None
    return packet


def project_purchase_library_notices(storage, rows, *, identity_id):
    """One bounded SQLite snapshot, no I/O on GTK, no supplier calls.

    Competing branch/right, stale receipt, detached account or malformed
    proof leaves native metadata unchanged. No title/ISBN/URL matching.
    """
    if type(rows) is not list:
        return
    for row in rows:
        if type(row) is dict:
            row.pop(_PACKET, None)
    if (type(identity_id) is not str or not identity_id
            or not 1 <= len(rows) <= _MAX_ROWS):
        return
    counts = Counter(row["key"] for row in rows if type(row) is dict
                     and type(row.get("key")) is str)
    wanted = [(row, key) for row in rows
              if (key := _coordinate(row, identity_id)) is not None
              and counts[row["key"]] == 1]
    local = [(row, pair) for row in rows if type(row) is dict
             and (pair := _local_coordinate(row, identity_id)) is not None
             and type(row.get('key')) is str and counts[row['key']] == 1]
    if not wanted and not local:
        return
    coordinates = {key for _, key in wanted}
    matches = {}
    staged = []
    try:
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            sources = db.execute(
                "SELECT provider_id,credential_id,account_id,edition_id,branch_id,right_id "
                "FROM source_references WHERE identity_id=? AND reference_kind='purchase' "
                "AND state='active' AND evidence_kind='provider_response' "
                "LIMIT ?", (identity_id, _MAX_ROWS + 1),
            ).fetchall()
            if len(sources) > _MAX_ROWS:
                return
            for record in sources:
                key = tuple(record[:4])
                if key in coordinates:
                    matches.setdefault(key, []).append(record[4:])
            facts = {}
            staged.extend(_local_notices_txn(db, local, identity_id))
            for row, key in wanted:
                candidates = matches.get(key, ())
                if len(candidates) != 1:
                    continue
                if key not in facts:
                    branch, right = candidates[0]
                    source = SourceReference(ProviderRightsScope(identity_id, *key[:3]),
                                             branch, key[3], right, "purchase")
                    notice = _read_descriptive_txn(db, source)
                    facts[key] = (source, notice)
                source, notice = facts[key]
                if notice is not None:
                    staged.append((row, PurchaseLibraryNotice(row["key"], source, notice)))
    except (sqlite3.Error, AttributeError, TypeError, ValueError, KeyError):
        return
    for row, packet in staged:
        row[_PACKET] = packet


def _local_notices_txn(db, local, identity_id):
    if not local:
        return []
    # Exact completed provider OriginLink, same owner and present Placement.
    # Do not infer a purchase from a matching title, item alias or SHA alone.
    records = db.execute(
        "SELECT DISTINCT p.artifact_id,p.placement_id,s.provider_id,"
        "s.credential_id,s.account_id,s.edition_id,s.branch_id,s.right_id "
        "FROM placements p JOIN artifacts a ON a.artifact_id=p.artifact_id "
        "AND a.artifact_id='sha256:'||a.sha256 AND a.size>0 "
        "AND length(a.sha256)=64 AND a.sha256 NOT GLOB '*[^0-9a-f]*' "
        "JOIN origin_links o ON o.artifact_id=p.artifact_id AND o.identity_id=p.identity_id "
        "JOIN source_references s ON s.source_reference_id=o.source_reference_id "
        "AND s.identity_id=o.identity_id WHERE p.identity_id=? AND p.state='present' "
        "AND o.origin_kind='provider' AND o.evidence_ref IS NOT NULL AND o.evidence_ref<>'' "
        "AND s.reference_kind='purchase' AND s.state='active' "
        "AND s.evidence_kind='provider_response' AND s.evidence_ref IS NOT NULL "
        "AND s.evidence_ref<>'' LIMIT ?", (identity_id, _MAX_ROWS + 1),
    ).fetchall()
    if len(records) > _MAX_ROWS:
        return []
    wanted = {pair for _, pair in local}
    matches = {}
    for record in records:
        pair = tuple(record[:2])
        if pair in wanted:
            matches.setdefault(pair, []).append(tuple(record[2:]))
    result = []
    for row, pair in local:
        claims = matches.get(pair, ())
        if len(claims) != 1:
            continue
        provider, credential, account, edition, branch, right = claims[0]
        source = SourceReference(ProviderRightsScope(identity_id, provider, credential, account),
                                 branch, edition, right, "purchase")
        notice = _read_descriptive_txn(db, source)
        if notice is not None:
            result.append((row, PurchaseLibraryNotice(row["key"], source, notice, pair)))
    return result
