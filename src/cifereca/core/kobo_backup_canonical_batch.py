"""#4858 W2: global Kobo command fans out through exactly scoped source owners.

Only the PUBLIC legacy `LibraryService.sync_kobo_backup` facade calls this
adapter. A modern Kobo session always uses the canonical A→B executor through
`sync_kobo_backup_account`; an incomplete ABI is refused by the retired W2 entry and never triggers an
opaque provider GET. No global W2 lock nests a canonical Sync lock.
"""
from __future__ import annotations

from typing import Any, Callable

from ..providers import ProviderConfigurationError
from .canonical_delivery_abi import has_complete_delivery_abi

_METRICS = (
    "scanned", "attempted", "downloaded", "unchanged", "completed",
    "pending", "attention_required", "skipped_archived", "errors",
    "remaining", "reconcile_pending", "reconcile_skipped",
    "blocked_non_retryable", "items_blocked_by_scope",
    "malformed_manifest_items", "duplicate_manifest_items",
    "provider_rejected_manifest_items",
)
_STATES = frozenset({
    "complete", "partial", "attention_required", "failed",
    "cancelled", "aborted",
})


def _safe_number(value):
    return value if type(value) is int and 0 <= value <= 1_000_000 else 0


def _id(value):
    return (
        type(value) is str and 0 < len(value) <= 256
        and value == value.strip() and value != "__public__"
        and all(ord(char) >= 32 and ord(char) != 127 for char in value)
    )


def _active_source_batch(service):
    selected = []
    for session in service._sessions.values():
        if (getattr(session, "provider_id", None) != "kobo"
                or getattr(session, "connected", None) is not True):
            continue
        canonical = has_complete_delivery_abi(session)
        if (not canonical
                and getattr(getattr(session, "capabilities", None),
                            "backup", None) is not True):
            continue
        selected.append((session, canonical))
    # When no connected source has a complete ABI, return None so that the
    # public facade refuses the retired W2 path. Mixed batches are dispatched
    # per account and incomplete-ABI accounts fail without legacy GET.
    if not any(canonical for _session, canonical in selected):
        return None
    credentials = [
        getattr(session, "credential_id", None)
        for session, _canonical in selected
    ]
    if (any(not _id(value) for value in credentials)
            or len(set(credentials)) != len(credentials)):
        raise ProviderConfigurationError("kobo_global_canonical_source_ambiguous")
    # Never silently select a session through a stale or alias key after
    # enumerating it: the per-account dispatcher repeats this admission.
    for (session, _canonical), credential in zip(selected, credentials):
        if service._sessions.get(service._session_key("kobo", credential)) is not session:
            raise ProviderConfigurationError("kobo_global_canonical_source_changed")
    return sorted(
        ((credential, session, canonical)
         for (session, canonical), credential in zip(selected, credentials)),
        key=lambda item: item[0],
    )


def _one_report(source: object):
    if type(source) is not dict:
        return {"status": "failed", "errors": 1}
    state = source.get("status")
    if state not in _STATES or type(state) is not str:
        return {"status": "failed", "errors": 1}
    # Counter fields from old provider workers may be malformed or derived
    # from their own messages. Only closed and bounded scalar metrics escape.
    return {
        "status": state,
        **{name: _safe_number(source.get(name)) for name in _METRICS},
    }


def run_global_kobo_with_canonical_sources(
    service: Any,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any] | None:
    """Run a modern/mixed global Kobo batch by EXACT credential.

    Return None only when no connected Kobo source has a complete ABI.
    Modern sources are exclusively canonical. Each account gets its own
    acquisition lease; a changed session is not dispatched by an old W2
    fallback. The public report has aggregate counts only.
    """
    batch = _active_source_batch(service)
    if batch is None:
        return None
    rows = []
    for index, (credential, session, modern) in enumerate(batch, start=1):
        try:
            if (service._sessions.get(service._session_key(
                    "kobo", credential,
                )) is not session or session.connected is not True
                    or (modern and not has_complete_delivery_abi(session))):
                raise ProviderConfigurationError("kobo_global_canonical_source_changed")
            raw = service.sync_kobo_backup_account(credential)
            if modern and (
                type(raw) is not dict
                or raw.get("execution_path") != "canonical"
                or raw.get("legacy_backup_mutations") != 0
            ):
                raise ProviderConfigurationError("kobo_global_canonical_executor_unavailable")
            row = _one_report(raw)
        except Exception:
            # A failed source cannot stop other user-selected credentials or
            # reveal provider exceptions, personal paths, bearer links, etc.
            row = {"status": "failed", "errors": 1}
        rows.append(row)
        if callable(progress):
            try:
                progress({
                    "event": "source_done", "position": index,
                    "total": len(batch), "status": row["status"],
                    "downloaded": _safe_number(row.get("downloaded")),
                    "errors": _safe_number(row.get("errors")),
                })
            except Exception:
                pass

    if rows and all(row["status"] == "complete" for row in rows):
        status = "complete"
    elif rows and all(row["status"] == "cancelled" for row in rows):
        status = "cancelled"
    elif rows and all(row["status"] == "failed" for row in rows):
        status = "failed"
    elif rows and all(row["status"] in {"attention_required", "complete"}
                      for row in rows):
        status = "attention_required"
    else:
        status = "partial"
    totals = {
        metric: min(1_000_000, sum(
            _safe_number(row.get(metric)) for row in rows
        )) for metric in _METRICS
    }
    summary = {
        "scope": "all", "status": status,
        "sessions": len(batch), "sources": rows,
        "canonical_sources": sum(1 for _, _, canonical in batch if canonical),
        "compatibility_sources": sum(1 for _, _, canonical in batch
                                      if not canonical),
        "aborted": False, "cancelled": status == "cancelled",
        "legacy_batch_writer": False,
        **totals,
    }
    if callable(progress):
        try:
            progress({
                "event": "done", "status": status,
                "downloaded": totals["downloaded"],
                "unchanged": totals["unchanged"],
                "attention_required": totals["attention_required"],
                "errors": totals["errors"],
            })
        except Exception:
            pass
    return summary


__all__ = ["run_global_kobo_with_canonical_sources"]
