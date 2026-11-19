"""Presentation-only purchase descriptions for exact remote Library cards."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import sqlite3
import time

from . import notice_c_job_journal as journal
from .notice_c_purchase_association import _current_a
from .notice_c_purchase_intent import _binding_txn, _allowed_txn

from ..plugin_api.downloadable_resources import SourceReference
from ..plugin_api.provider_rights_observation import ProviderRightsScope
from .notice_c_purchase_descriptive_read import PurchaseDescriptiveNotice, _read_descriptive_txn

_MAX_ROWS = 10_000
_PACKET = "_purchase_library_notice"
_STATUS = "_purchase_library_status"


@dataclass(frozen=True, slots=True, repr=False)
class PurchaseLibraryNotice:
    key: str
    source: SourceReference
    notice: PurchaseDescriptiveNotice
    placement: tuple[str, str] | None = None


@dataclass(frozen=True, slots=True, repr=False)
class PurchaseLibraryStatus:
    key: str
    source: SourceReference
    state: str
    attempts: int
    placement: tuple[str, str] | None = None


_STATUS_LABELS = {
    "pending": "En attente d’enrichissement",
    "running": "Enrichissement réservé (bail actif)",
    "retry_wait": "Nouvelle tentative différée",
    "failed": "Enrichissement non abouti",
    "proof_unavailable": "Preuve documentaire à revalider",
    "paused": "Requêtes d’enrichissement désactivées",
    "direct_locator_unattested": "Lien public direct non attesté",
    "comparison_title_unattested": "Titre de comparaison non attesté",
    "candidate_unqualified": "Candidat documentaire non qualifié",
}


def purchase_library_status(row):
    if type(row) is not dict:
        return None
    packet = row.get(_STATUS)
    if (type(packet) is not PurchaseLibraryStatus
            or type(packet.source) is not SourceReference
            or packet.source.kind != "purchase"
            or packet.key != row.get("key")
            or packet.state not in _STATUS_LABELS
            or type(packet.attempts) is not int
            or not 0 <= packet.attempts <= journal._MAX_ATTEMPTS):
        return None
    scope = packet.source.scope
    if packet.placement is not None:
        return packet if _local_coordinate(row, scope.identity_id) == packet.placement else None
    return packet if _coordinate(row, scope.identity_id) == (
        scope.provider_id, scope.credential_id, scope.account_id, packet.source.edition_id,
    ) else None


def purchase_status_label(packet):
    return _STATUS_LABELS.get(packet.state, "") if type(packet) is PurchaseLibraryStatus else ""


def _status_txn(db, source, *, now):
    # Job state is historical intent, NOT an active in-memory admission.
    # Only an explicit same-read omission may explain an absent candidate.
    # Missing job/receipt alone remains unknown.
    ref, proof = _current_a(db, source)
    if proof is None or not _binding_txn(db, source.scope):
        return None
    from .notice_c_purchase_omissions import current_omission_txn
    omitted = current_omission_txn(db, ref, proof)
    if omitted is not None:
        return omitted, 0
    target = journal._PURCHASE_TARGET + ref.removeprefix("source:")
    spec = journal.JobSpec("fetch_source_notice", target, "0" * 64,
                           journal._PURCHASE_INTENT_DIGEST)
    try:
        job = journal._load(db, journal._key(spec))
    except (ValueError, TypeError, KeyError, RecursionError):
        return None
    if job is None or job.spec.target != target or job.spec.implementation_digest != spec.implementation_digest:
        return None
    if not _allowed_txn(db, source.scope):
        state = "paused"
    elif job.state == "pending":
        state = ("running" if job.lease_until > now else
                 "retry_wait" if job.due > now and job.attempts else "pending")
    elif job.state == "error":
        state = "failed"
    else:
        state = "proof_unavailable"
    return state, job.attempts


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
            row.pop(_STATUS, None)
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
    statuses = []
    now = int(time.time())
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
            staged.extend(_local_notices_txn(db, local, identity_id, statuses=statuses, now=now))
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
                else:
                    state = _status_txn(db, source, now=now)
                    if state is not None:
                        statuses.append((row, PurchaseLibraryStatus(row["key"], source, *state)))
    except (sqlite3.Error, AttributeError, TypeError, ValueError, KeyError):
        return
    for row, packet in staged:
        row[_PACKET] = packet
    for row, packet in statuses:
        row[_STATUS] = packet


def _local_notices_txn(db, local, identity_id, *, statuses, now):
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
        else:
            state = _status_txn(db, source, now=now)
            if state is not None:
                statuses.append((row, PurchaseLibraryStatus(row["key"], source, *state, pair)))
    return result
