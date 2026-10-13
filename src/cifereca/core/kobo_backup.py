"""Kobo Vault backup coordination over existing artifact/runtime interfaces.

Manifest authority remains provider.kobo; acquisition execution remains the
negotiated kobo-adept contract. State is owned by the service facade."""
from __future__ import annotations

import time
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple, Callable
from ..acquisition import Acquisition
from ..kobo import KoboAdeptError
from ..kobo.executor import execute_kobo_acquisition
from ..kobo.vault import KoboVaultError
from ..locking import InterProcessLock, LockBusyError
from ..library_files import safe_library_component, sha256_file, try_sha256_file
from ..artifact_inspection import inspect_artifact
from ..paths import acquisition_lock_file
from ..models import SearchResult
from ..providers import ProviderConfigurationError
from ..plugin_api import UserFacingError


class _ManifestItem(NamedTuple):
    revision_id: str | None
    title: str
    account_id: str | None
    current: dict[str, Any] | None
    base_record: dict[str, Any]


def _observed_backup_artifact(path: Path) -> dict[str, Any]:
    inspection = inspect_artifact(path)
    if not inspection.valid or not inspection.media_format or inspection.protected is True:
        raise ProviderConfigurationError("Artefact de sauvegarde invalide ou encore protégé")
    return {"format": inspection.media_format.upper(), "size": path.stat().st_size, "sha256": sha256_file(path)}


def _kobo_artifact_plausible(path: Path, fmt: str) -> bool:
    """Perform a conservative local sanity check without interpreting DRM."""
    try:
        if not path.is_file() or path.stat().st_size < 4:
            return False
        with path.open("rb") as handle:
            head = handle.read(8)
    except OSError:
        return False
    if str(fmt).lower() == "pdf":
        return head.startswith(b"%PDF-")
    return head.startswith(b"PK\x03\x04")


def _kobo_record_candidate_paths(self, record: dict[str, Any], target_dir: Path) -> list[Path]:
    """Find filename candidates without trusting reported format or opening files.

    A manifest may switch EPUB/PDF between syncs. A candidate of either
    format must stop automated acquisition rather than silently duplicate it.
    """
    author = str(record.get("author") or "").strip()
    title = str(record.get("title") or record.get("item_id") or "Kobo").strip()
    stem = f"{author} - {title}" if author else title
    safe = self.acquisition._safe_filename(stem or str(record.get("item_id") or "kobo"))
    suffix = re.sub(r"[^A-Za-z0-9]", "", str(record.get("item_id") or ""))[-8:] or "kobo"
    paths: list[Path] = []
    for ext in (".epub", ".pdf"):
        for path in (target_dir / f"{safe}{ext}", target_dir / f"{safe} [{suffix}]{ext}"):
            # Even an invalid file or dangling symlink occupies the destination:
            # never overwrite it automatically as part of reconciliation.
            if path not in paths and (path.is_file() or path.is_symlink()):
                paths.append(path)
    return paths


def _kobo_destination(self, item: SearchResult, target_dir: Path, fmt: str) -> Path:
    ext = ".pdf" if fmt.lower() == "pdf" else ".epub"
    author = (item.author or "").strip()
    stem = f"{author} - {item.title}" if author else item.title
    safe = self.acquisition._safe_filename(stem or item.record_id)
    path = target_dir / f"{safe}{ext}"
    # Avoid an unrelated existing file blocking a different Kobo product.
    if path.exists():
        current = self.storage.backup_get("kobo", item.source_credential, item.record_id)
        if not current or Path(str(current.get("local_path") or "")) != path:
            suffix = re.sub(r"[^A-Za-z0-9]", "", item.record_id)[-8:] or "kobo"
            path = target_dir / f"{safe} [{suffix}]{ext}"
    return path


def _execute_kobo_acquisition(self, item: SearchResult, target_dir: Path) -> Acquisition:
    """Compatibility façade; Kobo execution lives in the provider adapter."""
    return execute_kobo_acquisition(self, item, target_dir)


def kobo_backup_records(self) -> list[dict[str, Any]]:
    return self.storage.backup_list("kobo")


def kobo_backup_enabled(self) -> bool:
    return bool(self.config.get("kobo_backup.enabled", False))


def kobo_backup_interval_minutes(self) -> int:
    try:
        return max(15, int(self.config.get("kobo_backup.interval_minutes", 60)))
    except (TypeError, ValueError):
        return 60


def _kobo_backup_target_dir(self, identity_id: str | None=None) -> Path:
    identity_id = identity_id or (self.current_identity.identity_id if self.current_identity else "default")
    configured = os.path.expandvars(os.path.expanduser(str(self.config.get("kobo_backup.directory", "") or "").strip()))
    if configured:
        base = Path(configured)
        if not base.is_absolute():
            base = self.library_dir() / base
        identity = self.identity_by_id(identity_id)
        directory_name = identity.directory_name if identity and identity.directory_name else identity_id
        path = base / safe_library_component(directory_name, "default") / "kobo"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return self.library_identity_provider_dir(identity_id, "kobo")


def _manifest_author(item: dict[str, Any]) -> str | None:
    authors = item.get("authors")
    if isinstance(authors, list):
        text = ", ".join(str(x) for x in authors if str(x).strip())
        return text or None
    text = str(item.get("author") or "").strip()
    return text or None


def _kobo_backup_error_info(exc: Exception) -> dict[str, Any]:
    """Return a compact, non-secret diagnostic classification for a backup error."""
    message = str(exc) or exc.__class__.__name__
    code: str | None = None
    status_code: int | None = None
    phase = "integration"
    fatal = False

    if isinstance(exc, KoboVaultError):
        phase = "vault"
        code = exc.code
        status_code = exc.status_code
        fatal = status_code in {401, 403} or str(code or "").lower() in {
            "missing_token", "invalid_token", "invalid_bearer", "unauthorized",
            "forbidden", "insufficient_scope", "invalid_api_token",
        }
    elif isinstance(exc, (KoboAdeptError, UserFacingError)):
        phase = "adept"
        code = str(getattr(exc, "code", "") or "adept_error")
    else:
        lower = message.lower()
        if "bearer token invalide" in lower or "token api refus" in lower:
            phase = "vault"
            code = "invalid_token"
            fatal = True
        elif "scope insuffisant" in lower:
            phase = "vault"
            code = "insufficient_scope"
            fatal = True
        elif "candidat epub/pdf http direct" in lower or "candidat http epub/pdf" in lower:
            phase = "integration"
            code = "no_compatible_http_candidate"
        elif "aucun artefact téléchargeable compatible" in lower:
            phase = "vault"
            code = "no_downloadable_artifact"
        elif "library_sync/kdrm exige" in lower:
            phase = "integration"
            code = "missing_pinned_delivery_capability"

    if code is None:
        code = exc.__class__.__name__
    return {
        "phase": phase,
        "code": str(code),
        "status_code": status_code,
        "message": message,
        "fatal": fatal,
    }


def kobo_backup_diagnostic(self) -> dict[str, Any]:
    """Summarise the persisted Kobo backup state without network access."""
    rows = self.storage.backup_list(provider_id="kobo")
    statuses = Counter(str(row.get("status") or "unknown") for row in rows)
    errors = Counter(
        str(row.get("error") or "").strip()
        for row in rows
        if str(row.get("status") or "") == "error" and str(row.get("error") or "").strip()
    )
    examples: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        message = str(row.get("error") or "").strip()
        if str(row.get("status") or "") != "error" or not message or message in seen:
            continue
        seen.add(message)
        examples.append({
            "item_id": row.get("item_id"),
            "title": row.get("title"),
            "error": message,
        })
        if len(examples) >= 10:
            break
    reconcile_examples = [
        {
            "item_id": row.get("item_id"),
            "title": row.get("title"),
            "status": row.get("status"),
            "local_path": row.get("local_path"),
            "message": row.get("error"),
        }
        for row in rows
        if str(row.get("status") or "") in {"reconcile_required", "reconcile_conflict"}
    ][:20]
    attention_examples = [
        {
            "item_id": row.get("item_id"),
            "title": row.get("title"),
            "attention_code": row.get("attention_code"),
            "message": row.get("error"),
        }
        for row in rows
        if str(row.get("status") or "") == "attention_required"
    ][:20]
    return {
        "items": len(rows),
        "statuses": dict(statuses),
        "error_summary": [
            {"message": message, "count": count}
            for message, count in errors.most_common(10)
        ],
        "error_examples": examples,
        "reconcile_examples": reconcile_examples,
        "attention_examples": attention_examples,
        "storage_schema_version": self.storage.schema_version(),
    }


def _kobo_backup_due(self) -> bool:
    try:
        interval_minutes = max(1, int(self.config.get("kobo_backup.interval_minutes", 60) or 60))
    except (TypeError, ValueError):
        interval_minutes = 60
    raw = self.storage.metadata_get("kobo_backup_last_success_at")
    try:
        last = int(raw or 0)
    except (TypeError, ValueError):
        last = 0
    return last <= 0 or (int(time.time()) - last) >= interval_minutes * 60


def sync_kobo_backup(self, progress: Callable[[dict[str, Any]], None] | None=None, *, lock_factory, lock_path) -> dict[str, Any]:
    """Compatibility entry point for the Kobo local synchronization.

    0.8.0 keeps the validated cross-process serialization for acquisition work. The
    same global lock is intentionally named for local acquisition rather
    than Kobo so the 0.8.x loan synchronizer can share it.
    """
    lock = lock_factory(lock_path())
    try:
        with lock:
            return self._sync_kobo_backup_locked(progress=progress)
    except LockBusyError as exc:
        raise ProviderConfigurationError(
            "Une synchronisation locale est déjà en cours dans une autre instance de cifereca"
        ) from exc


def kobo_unlinked_local_count(self, identity_id: str) -> int:
    """Conservative guard, never infer a Kobo link from a filename/title."""
    known = {str(Path(r["local_path"]).resolve()) for r in self.storage.backup_list("kobo")
             if r.get("identity_id") == identity_id and r.get("local_path")}
    default_id = self.identities[0].identity_id if self.identities else None
    paths = set()
    for row in self.storage.catalog_entries():
        owner = row.get("identity_id") or default_id
        raw = (row.get("metadata") or {}).get("path")
        if (owner == identity_id and row.get("local_state") == "local"
                and str(row.get("provider_id") or "local") in {"local", "kobo"} and raw):
            path = Path(raw)
            if path.is_file() and str(path.resolve()) not in known:
                paths.add(str(path.resolve()))
    return len(paths)


def _sync_kobo_backup_locked(self, progress: Callable[[dict[str, Any]], None] | None=None, *, logger) -> dict[str, Any]:
    """Reconcile the Vault manifest and acquire missing Kobo books locally.

    Paris owns the destination and orchestration. Vault Provider API v2.x
    resolves Kobo acquisition information; Kobo-Adept's negotiated public
    contract executes the local download. Neither supplier drives the other.

    0.8.0 keeps Vault API 2.3 semantics and Adept contract v8+, but candidate policy/execution now live in the generic engine and Kobo adapter. It keeps
    transport selection with candidate.handling authoritative and adds Vault 2.3.3 library_sync pinned KDRM bridging:
    ACSM/ADEPT first, then SignedNoDrm/http_plain by direct HTTP, then true
    KDRM/kobo_kdrm through delegated Kobo execution. Explicit user-attention
    states and cross-process serialization remain unchanged. Adept contract v8 remains the preferred local executor;
    existing reconciliation safeguards still prevent duplicate redownloads.
    """
    def emit(event: str, **detail: Any) -> None:
        if progress is None:
            return
        try:
            progress({"event": event, **detail})
        except Exception:
            # Progress reporting must never break the backup itself.
            pass

    # Refresh filesystem membership before deciding whether acquisition is safe.
    self.catalog_scan()
    sessions = [
        session for session in self._sessions.values()
        if session.connected and session.provider_id == "kobo" and session.capabilities.backup
    ]
    target_dir = self._kobo_backup_target_dir()
    target_dirs: dict[str, str] = {}
    preferred = str(self.config.get("kobo_backup.format", "epub") or "epub").lower()
    vault_versions = sorted({
        str(getattr(session, "api_version", "") or "2.x") for session in sessions
    })
    vault_contract = ",".join(vault_versions) if vault_versions else "2.x"
    emit("adept_probe", message="Vérification de Kobo-Adept")
    adept_state = self.kobo_adept_state(force=True)
    emit(
        "adept_ready",
        compatible=bool(adept_state.get("compatible")),
        operational=bool(adept_state.get("operational")),
        software_version=adept_state.get("software_version"),
        contract_version=adept_state.get("contract_version"),
    )
    adapter_available = bool(adept_state.get("available") and adept_state.get("compatible") and adept_state.get("operational"))
    summary: dict[str, Any] = {
        "sessions": len(sessions), "manifest_items": 0, "scanned": 0, "attempted": 0, "pending": 0,
        "completed": 0, "downloaded": 0, "unchanged": 0, "metadata_refreshed": 0,
        "skipped": 0, "attention_required": 0, "errors": 0, "remaining": 0,
        "vault_errors": 0, "integration_errors": 0, "adept_errors": 0,
        "recovered_interrupted": 0, "recovered_unparsed": 0,
        "reconcile_pending": 0, "reconcile_conflicts": 0, "reconcile_skipped": 0,
        "legacy_pruned": 0, "legacy_local_preserved": 0, "legacy_entitlements_inactivated": 0,
        "aborted": False, "abort_reason": None,
        "target_dir": str(target_dir),
        "target_dirs": target_dirs,
        "storage_schema_version": self.storage.schema_version(),
        "vault_contract": vault_contract,
        "vault_display_profile": str(self.config.get("kobo_backup.display_profile", "Android") or "Android"),
        "vault_acsm_supported": any(bool(getattr(session, "supports_acquisition_acsm", False)) for session in sessions),
        "adept_acsm_available": bool(adept_state.get("acsm_available")),
        "adept_pinned_delivery_candidate": bool(adept_state.get("kobo_pinned_delivery_candidate")),
        "adept_separate_content_keys_replay": bool(adept_state.get("kobo_separate_content_keys_replay")),
        "adept_kdrm_available": bool(adept_state.get("kobo_kdrm_available")),
        "adept_kobo_formats": list(adept_state.get("kobo_formats") or []),
        "adept_profile_configured": any(
            bool(self.storage.adept_profile_binding_get("identity", identity.identity_id))
            for identity in self.identities
        ),
        "adept_contract": adept_state.get("contract_version") or 8,
        "adept_contract_min": 8,
        "adept_contract_preferred": 8,
        "adept_available": adapter_available,
        "adept_software_version": adept_state.get("software_version"),
        "adept_error": adept_state.get("error"),
    }

    # Reconcile local remnants from interrupted / previously-unparseable
    # acquisitions before contacting Vault again. Never delete a file here:
    # one plausible artifact is quarantined for verification; several
    # plausible artifacts become an explicit conflict. This prevents the
    # historical ``Title [deadbeef].epub`` duplication loop.
    _recover_local_remnants(self, summary, target_dirs, emit)
    error_counts: Counter[tuple[str, str, int | None, str]] = Counter()
    error_examples: list[dict[str, Any]] = []

    def record_error(exc: Exception, *, title: str | None = None, item_id: str | None = None) -> dict[str, Any]:
        info = self._kobo_backup_error_info(exc)
        phase = str(info.get("phase") or "integration")
        phase_counter = {"vault": "vault_errors", "integration": "integration_errors", "adept": "adept_errors"}.get(phase)
        if phase_counter:
            summary[phase_counter] += 1
        key = (phase, str(info["code"]), info.get("status_code"), str(info["message"]))
        error_counts[key] += 1
        if len(error_examples) < 10:
            example = {
                "phase": info["phase"], "code": info["code"],
                "status_code": info.get("status_code"), "message": info["message"],
            }
            if item_id:
                example["item_id"] = item_id
            if title:
                example["title"] = title
            error_examples.append(example)
        return info

    abort_all = False
    authoritative_manifest_ids: dict[str, set[str]] = {}
    for session in sessions:
        session_identity = self.identity_for_connection("kobo", session.credential_id)
        unlinked_local_count = self.kobo_unlinked_local_count(session_identity.identity_id)
        session_target_dir = self._kobo_backup_target_dir(session_identity.identity_id)
        target_dirs[session_identity.identity_id] = str(session_target_dir)
        try:
            emit("manifest_fetch", credential_id=session.credential_id, message="Lecture du manifest Kobo-Vault")
            manifest = session.backup_manifest()
            summary["manifest_items"] += len(manifest)
            authoritative_manifest_ids[session.credential_id] = {
                str(raw.get("product_id") or raw.get("id") or "").strip()
                for raw in manifest if isinstance(raw, dict)
                if str(raw.get("product_id") or raw.get("id") or "").strip()
            }
            emit("manifest", credential_id=session.credential_id, total=len(manifest))
        except Exception as exc:
            summary["errors"] += 1
            info = record_error(exc, title=session.label)
            if info["fatal"]:
                summary["aborted"] = True
                summary["abort_reason"] = info
                abort_all = True
                break
            continue

        for index, raw in enumerate(manifest):
            if not isinstance(raw, dict):
                continue
            summary["scanned"] += 1
            progress_title = str(raw.get("title") or raw.get("product_id") or raw.get("id") or "Kobo")
            emit(
                "scan",
                current=index + 1,
                total=len(manifest),
                title=progress_title,
                downloaded=summary["downloaded"],
                errors=summary["errors"],
                unchanged=summary["unchanged"],
                skipped=summary["skipped"],
            )
            product_id = str(raw.get("product_id") or raw.get("id") or "").strip()
            if not product_id:
                summary["skipped"] += 1
                continue
            content_type = str(raw.get("content_type") or "ebook").lower()
            if content_type not in {"ebook", "book", "epub"}:
                summary["skipped"] += 1
                continue
            item = _refresh_manifest_record(self, session, session_identity, raw, product_id, summary)
            if _skip_manifest_item(self, item, raw, session_target_dir, unlinked_local_count, adapter_available, adept_state, index, len(manifest), summary, emit, logger):
                continue

            if _acquire_manifest_item(self, session, session_identity, item, raw, session_target_dir, index, len(manifest), summary, emit, record_error, logger):
                abort_all = True
                break
        if abort_all:
            break

    # ``backup_items`` is a current-manifest compatibility projection.
    # Prune it only after the complete Kobo run succeeded: a failed/partial
    # sync must never make a remote title disappear merely because its
    # manifest could not be observed.  Artifacts/placements/catalog rows are
    # deliberately preserved by Storage.backup_prune_missing().
    _prune_successful_manifest(self, summary, authoritative_manifest_ids, emit)

    return _publish_summary(self, summary, error_counts, error_examples, emit)

def _recover_local_remnants(self, summary, target_dirs, emit):
    for previous in self.storage.backup_list("kobo"):
        previous_status = str(previous.get("status") or "")
        previous_error = str(previous.get("error") or "")
        recoverable = (
            previous_status in {"acquiring", "reconcile_required", "reconcile_conflict"}
            or (previous_status == "error" and "réponse JSON invalide" in previous_error)
        )
        if not recoverable:
            continue
        if previous_status == "acquiring":
            summary["recovered_interrupted"] += 1
        if previous_status == "error" and "réponse JSON invalide" in previous_error:
            summary["recovered_unparsed"] += 1

        previous_identity = str(previous.get("identity_id") or "").strip() or self.identity_for_connection(
            str(previous.get("provider_id") or "kobo"), str(previous.get("credential_id") or "")
        ).identity_id
        previous_target_dir = self._kobo_backup_target_dir(previous_identity)
        target_dirs[previous_identity] = str(previous_target_dir)
        candidates = self._kobo_record_candidate_paths(previous, previous_target_dir)
        # Compatibility scan: legacy flat files may still exist after an interrupted
        # reclassification; include them to avoid duplicate downloads.
        legacy_target_dir = self.library_dir()
        if legacy_target_dir.resolve() != previous_target_dir.resolve():
            for legacy_candidate in self._kobo_record_candidate_paths(previous, legacy_target_dir):
                if legacy_candidate not in candidates:
                    candidates.append(legacy_candidate)
        if len(candidates) == 1:
            artifact = candidates[0]
            self.storage.backup_put({
                **previous,
                "status": "reconcile_required",
                "local_path": str(artifact),
                "size": artifact.stat().st_size,
                "sha256": try_sha256_file(artifact),
                "updated_at": int(time.time()),
                "error": (
                    "Artefact local présent après une acquisition Adept non réconciliée ; "
                    "vérification post-hook requise avant de le considérer complet"
                ),
                "job_id": None,
            })
            emit("reconcile_required", item_id=previous.get("item_id"), title=previous.get("title"), path=str(artifact))
        elif len(candidates) > 1:
            self.storage.backup_put({
                **previous,
                "status": "reconcile_conflict",
                "local_path": None,
                "updated_at": int(time.time()),
                "error": (
                    f"{len(candidates)} artefacts locaux candidats détectés ; "
                    "aucune suppression automatique"
                ),
                "job_id": None,
            })
            emit("reconcile_conflict", item_id=previous.get("item_id"), title=previous.get("title"), count=len(candidates))
        elif previous_status == "acquiring":
            self.storage.backup_put({
                **previous,
                "status": "pending_acquisition",
                "updated_at": int(time.time()),
                "error": "Acquisition locale interrompue avant finalisation ; nouvelle tentative nécessaire",
                "job_id": None,
            })
        elif previous_status in {"reconcile_required", "reconcile_conflict"}:
            # The user may have manually moved/removed the suspect file(s).
            # With no candidate left, allow a clean retry on the next pass.
            self.storage.backup_put({
                **previous,
                "status": "pending_acquisition",
                "local_path": None,
                "size": None,
                "sha256": None,
                "updated_at": int(time.time()),
                "error": "Artefact à réconcilier absent ; nouvelle tentative autorisée",
                "job_id": None,
            })

    recovery_rows = self.storage.backup_list("kobo")
    summary["reconcile_pending"] = sum(
        1 for row in recovery_rows
        if str(row.get("status") or "") in {"reconcile_required", "reconcile_conflict"}
    )
    summary["reconcile_conflicts"] = sum(
        1 for row in recovery_rows
        if str(row.get("status") or "") == "reconcile_conflict"
    )
    if summary["recovered_interrupted"]:
        emit("recovered", count=summary["recovered_interrupted"])
    if summary["recovered_unparsed"]:
        emit("recovered_unparsed", count=summary["recovered_unparsed"])



def _prune_successful_manifest(self, summary, authoritative_manifest_ids, emit):
    if not summary.get("errors") and not summary.get("aborted"):
        for credential_id, seen_ids in authoritative_manifest_ids.items():
            pruned = self.storage.backup_prune_missing("kobo", credential_id, seen_ids)
            summary["legacy_pruned"] += int(pruned.get("pruned") or 0)
            summary["legacy_local_preserved"] += int(pruned.get("local_preserved") or 0)
            summary["legacy_entitlements_inactivated"] += int(pruned.get("entitlements_inactivated") or 0)
            if pruned.get("pruned"):
                emit(
                    "legacy_pruned", credential_id=credential_id,
                    count=pruned.get("pruned"), local_preserved=pruned.get("local_preserved"),
                )




def _publish_summary(self, summary, error_counts, error_examples, emit):
    summary["error_summary"] = [
        {
            "phase": phase, "code": code, "status_code": status_code,
            "message": message, "count": count,
        }
        for (phase, code, status_code, message), count in error_counts.most_common(10)
    ]
    summary["error_examples"] = error_examples
    # Backward-compatible concise text list for existing callers.
    summary["error_messages"] = [x["message"] for x in error_examples[:10]]
    self.diagnostics.add(
        "KOBO", "backup sync", "ok" if not summary["errors"] else "error",
        detail={k: v for k, v in summary.items() if k != "error_messages"},
    )
    emit("done", **{k: v for k, v in summary.items() if k not in {"error_examples", "error_messages"}})
    if not summary.get("errors") and not summary.get("aborted"):
        self.storage.metadata_set("kobo_backup_last_success_at", int(time.time()))
    return summary



def _refresh_manifest_record(self, session, session_identity, raw, product_id, summary) -> _ManifestItem:
    revision_id = str(raw.get("revision_id") or "").strip() or None
    title = str(raw.get("title") or product_id)
    author = self._manifest_author(raw)
    fmt = str(raw.get("format") or "").upper() or None
    current = self.storage.backup_get("kobo", session.credential_id, product_id)
    account_id = session.accounts[0].unique_id if len(session.accounts) == 1 else None
    fresh_cover_url = session.cover_url(raw) if hasattr(session, "cover_url") else None

    base_record = {
        "provider_id": "kobo",
        "credential_id": session.credential_id,
        "account_id": account_id,
        "identity_id": session_identity.identity_id,
        "entitlement_type": "owned",
        "item_id": product_id,
        "revision_id": revision_id,
        "title": title,
        "author": author,
        "format": fmt,
        "cover_url": fresh_cover_url or (current.get("cover_url") if current else None),
        "local_path": current.get("local_path") if current else None,
        "size": current.get("size") if current else None,
        "sha256": current.get("sha256") if current else None,
        "updated_at": int(time.time()),
        "job_id": None,
        "attention_code": None,
    }

    # Metadata refresh is independent from acquisition. Historical
    # backup rows may have been persisted before Vault exposed a
    # normalized cover_url. Refresh the durable projection before any
    # fast-path continue so title/author/format/cover metadata can heal
    # without re-downloading an unchanged local artifact.
    if current:
        refreshed = {
            **current,
            **base_record,
            "status": current.get("status") or "pending",
            "error": current.get("error"),
            "job_id": current.get("job_id"),
            "attention_code": current.get("attention_code"),
            "attempt_count": current.get("attempt_count"),
            "last_attempt_at": current.get("last_attempt_at"),
            "acquisition_strategy": current.get("acquisition_strategy"),
            "executor": current.get("executor"),
            "adept_profile": current.get("adept_profile"),
            "loan_id": current.get("loan_id"),
            "expires_at": current.get("expires_at"),
        }
        before_cover = str(current.get("cover_url") or "").strip()
        after_cover = str(refreshed.get("cover_url") or "").strip()
        if (
            before_cover != after_cover
            or current.get("title") != refreshed.get("title")
            or current.get("author") != refreshed.get("author")
            or current.get("format") != refreshed.get("format")
            or current.get("identity_id") != refreshed.get("identity_id")
        ):
            summary["metadata_refreshed"] += 1
        self.storage.backup_put(refreshed)
        current = self.storage.backup_get("kobo", session.credential_id, product_id) or refreshed

    return _ManifestItem(revision_id, title, account_id, current, base_record)



def _skip_manifest_item(self, item, raw, session_target_dir, unlinked_local_count, adapter_available, adept_state, index, total, summary, emit, logger):
    revision_id, title, account_id, current, base_record = item
    product_id = base_record["item_id"]
    if current and str(current.get("status") or "") in {"reconcile_required", "reconcile_conflict"}:
        summary["reconcile_skipped"] += 1
        emit(
            "reconcile_waiting",
            current=index + 1, total=total, title=title, item_id=product_id,
            status=current.get("status"), path=current.get("local_path"),
        )
        return True

    if current and current.get("status") in {"ok", "complete"} and current.get("revision_id") == revision_id:
        local = Path(str(current.get("local_path") or ""))
        if local.is_file():
            try:
                observed = self._observed_backup_artifact(local)
            except ProviderConfigurationError:
                # A stale/invalid file must not retain a complete state.
                self.storage.backup_put({**current, "status": "reconcile_required", "error": "Artefact local invalide"})
                summary["reconcile_skipped"] += 1
                return True
            self.storage.backup_put({**current, **observed})
            summary["unchanged"] += 1
            return True

    # A prior Kobo row may point to a nonstandard filename. Even when the
    # remote revision changes, never discard that placement or redownload
    # until a deliberate replacement/reconciliation contract exists.
    if current and current.get("local_path"):
        linked_path = Path(str(current["local_path"]))
        if linked_path.is_file():
            self.storage.backup_put({
                **base_record,
                "status": "reconcile_required",
                "local_path": str(linked_path),
                "error": "Artefact Kobo déjà lié : révision distante différente ou état incomplet ; rapprochement requis",
            })
            summary["reconcile_pending"] += 1
            summary["reconcile_skipped"] += 1
            emit("reconcile_required", current=index + 1, total=total,
                 title=title, item_id=product_id)
            return True

    # A bypass of the global unlinked-books guard must never imply permission
    # to overwrite or re-download a plausible, *unlinked* artifact for this item.
    # Filename matches, including damaged or symlinked files, are ambiguous:\n    # quarantine for manual reconciliation
    # rather than assigning ownership or marking an uninspected file complete.
    # This includes legacy flat-library placements from older versions.
    candidates = self._kobo_record_candidate_paths(base_record, session_target_dir)
    legacy_dir = self.library_dir()
    if legacy_dir.resolve() != session_target_dir.resolve():
        for candidate in self._kobo_record_candidate_paths(base_record, legacy_dir):
            if candidate not in candidates:
                candidates.append(candidate)
    if candidates:
        conflict = len(candidates) > 1
        self.storage.backup_put({
            **base_record,
            "status": "reconcile_conflict" if conflict else "reconcile_required",
            "local_path": None if conflict else str(candidates[0]),
            "error": "Fichier(s) locaux candidats : rapprochement manuel requis avant téléchargement",
        })
        summary["reconcile_pending"] += 1
        if conflict:
            summary["reconcile_conflicts"] += 1
        summary["reconcile_skipped"] += 1
        emit("reconcile_conflict" if conflict else "reconcile_required",
             current=index + 1, total=total, item_id=product_id, title=title,
             count=len(candidates))
        return True

    # Vault 2.3.1 exposes archived items explicitly. Keep an already
    # acquired local file (handled above), otherwise persist a user
    # action instead of treating the title as permanently unavailable.
    if bool(raw.get("archived")):
        message = "Livre archivé sur Kobo ; restaurez-le dans votre bibliothèque Kobo avant de réessayer"
        self.storage.backup_put({
            **base_record,
            "status": "attention_required",
            "attention_code": "archived_book",
            "error": message,
        })
        summary["attention_required"] += 1
        emit(
            "attention_required", current=index + 1, total=total,
            title=title, item_id=product_id, code="archived_book", message=message,
        )
        return True

    if raw.get("downloadable") is False:
        summary["skipped"] += 1
        return True

    if unlinked_local_count and not bool(self.config.get("kobo_backup.allow_unlinked_acquisition", False)):
        message = f"Acquisition suspendue : {unlinked_local_count} livre(s) local(aux) sans liaison Kobo. Rapprochez les fichiers ou autorisez explicitement l’acquisition dans les paramètres."
        self.storage.backup_put({**base_record, "status": "attention_required", "attention_code": "unlinked_local_books", "error": message})
        summary["attention_required"] += 1
        emit("attention_required", current=index + 1, total=total, title=title, item_id=product_id, code="unlinked_local_books", message=message)
        logger.info("TRACE kobo acquisition decision=blocked reason=unlinked-local-books count=%s", unlinked_local_count)
        return True

    if not adapter_available:
        adept_error = adept_state.get("error") if isinstance(adept_state.get("error"), dict) else {}
        reason = str(adept_error.get("message") or "Kobo-Adept indisponible")
        self.storage.backup_put({**base_record, "status": "pending_acquisition", "error": reason})
        summary["pending"] += 1
        return True

    return False



def _acquire_manifest_item(self, session, session_identity, item, raw, session_target_dir, index, total, summary, emit, record_error, logger):
    revision_id, title, account_id, current, base_record = item
    product_id = base_record["item_id"]
    summary["attempted"] += 1
    attempt_count = int(current.get("attempt_count") or 0) + 1 if current else 1
    attempted_at = int(time.time())
    self.storage.backup_put({
        **base_record,
        "status": "acquiring",
        "error": None,
        "attempt_count": attempt_count,
        "last_attempt_at": attempted_at,
        "executor": "kobo-adept",
    })
    emit("acquiring", current=index + 1, total=total, title=title, item_id=product_id)
    try:
        normalizer = getattr(session, "normalize_item", None)
        if normalizer is None:
            raise ProviderConfigurationError("Kobo provider sans normalisation LibraryItem")
        search_item = normalizer(raw, default_collection="library")
        result = self._execute_kobo_acquisition(search_item, session_target_dir)
        artifact = result.path
        if artifact is None or not artifact.is_file():
            raise ProviderConfigurationError("Acquisition Kobo sans fichier local")
        observed = self._observed_backup_artifact(artifact)
        size = observed["size"]
        sha256 = observed["sha256"]
        result_metadata = result.metadata if isinstance(result.metadata, dict) else {}
        self.storage.backup_put({
            **base_record,
            "account_id": result.account_id or account_id,
            "format": observed["format"],
            "status": "complete",
            "local_path": str(artifact),
            "size": size if size is not None else artifact.stat().st_size,
            "sha256": sha256,
            "updated_at": int(time.time()),
            "error": None,
            "attempt_count": attempt_count,
            "last_attempt_at": attempted_at,
            "acquisition_strategy": result_metadata.get("acquisition_mode"),
            "executor": "kobo-adept",
            "adept_profile": result_metadata.get("adept_profile"),
        })
        summary["downloaded"] += 1
        summary["completed"] += 1
        emit(
            "downloaded", current=index + 1, total=total, title=title,
            item_id=product_id, path=str(artifact), downloaded=summary["downloaded"],
            errors=summary["errors"],
        )
    except Exception as exc:
        if isinstance(exc, (KoboAdeptError, UserFacingError)) and getattr(exc, "code", "") == "result_unparseable_artifact_present":
            details = dict(getattr(exc, "details", {}) or {})
            artifact_path = Path(str(details.get("artifact_path") or "")) if details.get("artifact_path") else None
            self.storage.backup_put({
                **base_record,
                "status": "reconcile_required",
                "local_path": str(artifact_path) if artifact_path else None,
                "size": details.get("artifact_size"),
                "sha256": details.get("artifact_sha256"),
                "error": str(exc),
                "updated_at": int(time.time()),
            })
            summary["reconcile_pending"] += 1
            emit(
                "reconcile_required",
                current=index + 1, total=total, title=title, item_id=product_id,
                path=str(artifact_path) if artifact_path else None,
            )
            return False
        info = self._kobo_backup_error_info(exc)
        if str(info.get("code") or "") == "archived_book":
            message = "Livre archivé sur Kobo ; restaurez-le dans votre bibliothèque Kobo avant de réessayer"
            self.storage.backup_put({
                **base_record,
                "status": "attention_required",
                "attention_code": "archived_book",
                "error": message,
                "attempt_count": attempt_count,
                "last_attempt_at": attempted_at,
                "executor": "kobo-adept",
                "updated_at": int(time.time()),
            })
            summary["attention_required"] += 1
            emit(
                "attention_required", current=index + 1, total=total,
                title=title, item_id=product_id, code="archived_book", message=message,
            )
            return False
        message = str(exc)
        logger.warning(
            "TRACE kobo acquisition failed identity=%s credential=%s item_id=%s exception=%s code=%s phase=%s status=%s error=%s",
            session_identity.identity_id, session.credential_id, product_id, type(exc).__name__,
            info.get("code"), info.get("phase"), info.get("status_code"), message,
        )
        self.storage.backup_put({
            **base_record, "status": "error", "error": message,
            "attempt_count": attempt_count, "last_attempt_at": attempted_at,
            "executor": "kobo-adept", "updated_at": int(time.time()),
        })
        summary["errors"] += 1
        info = record_error(exc, title=title, item_id=product_id)
        emit(
            "item_error", current=index + 1, total=total, title=title,
            item_id=product_id, code=info.get("code"), phase=info.get("phase"),
            message=info.get("message"), downloaded=summary["downloaded"], errors=summary["errors"],
        )
        if info["fatal"]:
            summary["aborted"] = True
            summary["abort_reason"] = info
            summary["remaining"] += max(0, total - index - 1)
            return True
    return False

