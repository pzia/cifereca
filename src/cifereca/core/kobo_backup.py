"""Kobo Vault backup coordination over existing artifact/runtime interfaces.

Manifest authority remains provider.kobo; acquisition execution remains the
negotiated kobo-adept contract. State is owned by the service facade."""
from __future__ import annotations

from .source_context import source_context_epoch

import time
import os
import re
import hashlib
import json
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
from ..plugin_api import UserFacingError, ProviderError


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

    A manifest may switch EPUB/PDF between syncs. A Kobo-enhanced EPUB
    can also be named .kepub.epub: it is a real local candidate even though
    the suffix is compound. Any such candidate blocks a duplicate download.
    """
    author = str(record.get("author") or "").strip()
    title = str(record.get("title") or record.get("item_id") or "Kobo").strip()
    stem = f"{author} - {title}" if author else title
    safe = self.acquisition._safe_filename(stem or str(record.get("item_id") or "kobo"))
    suffix = re.sub(r"[^A-Za-z0-9]", "", str(record.get("item_id") or ""))[-8:] or "kobo"
    paths: list[Path] = []
    for ext in (".epub", ".pdf", ".kepub.epub"):
        for path in (target_dir / f"{safe}{ext}", target_dir / f"{safe} [{suffix}]{ext}"):
            # Even an invalid file or dangling symlink occupies the destination:
            # never overwrite it automatically as part of reconciliation.
            if path not in paths and (path.is_file() or path.is_symlink()):
                paths.append(path)
    return paths



def _indexed_kobo_catalog_paths(self) -> dict[tuple[str, str], list[Path]]:
    """Index only explicit local Kobo product-ID links within one identity.

    Titles, ISBNs and filenames are not proof of a Kobo entitlement.
    A matching local catalog entry is a reason to stop, never to
    automatically adopt its artifact as a complete Kobo backup.
    """
    default_identity_id = self.identities[0].identity_id if self.identities else None
    indexed: dict[tuple[str, str], list[Path]] = {}
    for row in self.storage.catalog_entries():
        if row.get("local_state") != "local" or row.get("provider_id") != "kobo":
            continue
        identity = str(row.get("identity_id") or default_identity_id or "")
        item_id = str(row.get("item_id") or "").strip()
        metadata = row.get("metadata")
        path_text = metadata.get("path") if isinstance(metadata, dict) else None
        if not identity or not item_id or not isinstance(path_text, str) or not path_text.strip():
            continue
        path = Path(path_text)
        if path.is_file() or path.is_symlink():
            bucket = indexed.setdefault((identity, item_id), [])
            if path not in bucket:
                bucket.append(path)
    return indexed


def _kobo_destination(self, item: SearchResult, target_dir: Path, fmt: str) -> Path:
    ext = ".pdf" if fmt.lower() == "pdf" else ".epub"
    author = (item.author or "").strip()
    stem = f"{author} - {item.title}" if author else item.title
    safe = self.acquisition._safe_filename(stem or item.record_id)
    path = target_dir / f"{safe}{ext}"
    # Avoid an unrelated existing file blocking a different Kobo product.
    if path.exists() or path.is_symlink():
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
    # Fail closed for a manually edited/migrated malformed config:
    # bool("false") would otherwise trigger implicit local downloads.
    return self.config.get("kobo_backup.enabled", False) is True


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


# The provider/runtime chooses exception.code and even its class name.
# Identifier-like strings are NOT necessarily safe (an account can be one).
_KOBO_BACKUP_CODES = frozenset({
    "missing_delegation_scope", "missing_token", "invalid_token",
    "invalid_bearer", "unauthorized", "forbidden", "insufficient_scope",
    "invalid_api_token", "archived_book", "temporary", "network_error",
    "http_error", "redirect_denied", "invalid_response",
    "invalid_pagination", "no_downloadable_artifact",
    "no_compatible_http_candidate", "missing_pinned_delivery_capability",
    "result_unparseable_artifact_present",
    "result_unparseable_artifact_missing", "invalid_epub",
})


def _kobo_backup_error_info(exc: Exception) -> dict[str, Any]:
    """Only bounded, closed diagnostic facts cross the backup/error boundary."""
    code: str | None = None
    status_code: int | None = None
    phase = "integration"
    fatal = False

    if isinstance(exc, KoboVaultError) or (
        isinstance(exc, ProviderError)
        and type(getattr(exc, "status_code", None)) is int
    ):
        phase = "vault"
        raw_code = getattr(exc, "code", None)
        code = (raw_code if type(raw_code) is str
                and raw_code in _KOBO_BACKUP_CODES else "provider_http_error")
        raw_status = getattr(exc, "status_code", None)
        status_code = (raw_status if type(raw_status) is int
                       and 100 <= raw_status <= 599 else None)
        # Delegated-only 403 does not invalidate a previously authenticated
        # account, and must not starve other owned Kobo purchases.
        fatal = code != "missing_delegation_scope" and (
            status_code in {401, 403} or code in {
                "missing_token", "invalid_token", "invalid_bearer",
                "unauthorized", "forbidden", "insufficient_scope",
                "invalid_api_token",
            }
        )
    elif isinstance(exc, (KoboAdeptError, UserFacingError)):
        phase = "adept"
        raw_code = getattr(exc, "code", None)
        code = (raw_code if type(raw_code) is str
                and raw_code in _KOBO_BACKUP_CODES else "adept_error")
    else:
        # Legacy untyped exceptions retain the historical coarse auth/scope
        # classification. Their text is NEVER copied to a receipt or event.
        try:
            hint = str(exc)[:512].lower()
        except Exception:
            hint = ""
        if "bearer token invalide" in hint or "token api refus" in hint:
            phase, code, fatal = "vault", "invalid_token", True
        elif "scope insuffisant" in hint:
            phase, code, fatal = "vault", "insufficient_scope", True
        elif "candidat epub/pdf http direct" in hint or "candidat http epub/pdf" in hint:
            code = "no_compatible_http_candidate"
        elif "aucun artefact téléchargeable compatible" in hint:
            phase, code = "vault", "no_downloadable_artifact"
        elif "library_sync/kdrm exige" in hint:
            code = "missing_pinned_delivery_capability"

    return {
        "phase": phase,
        "code": code or "backup_item_failed",
        "status_code": status_code,
        "message": "Sauvegarde Kobo impossible",
        "fatal": fatal,
    }


def _kobo_preview_paths(self, identity_id: str) -> tuple[Path, Path]:
    """Derive Kobo paths without creating a directory or touching metadata."""
    configured_root = os.path.expandvars(os.path.expanduser(
        str(self.config.get("library.directory", "~/EBooks") or "~/EBooks").strip()
    ))
    library_root = Path(configured_root)
    if not library_root.is_absolute():
        library_root = Path.home() / library_root

    configured = os.path.expandvars(os.path.expanduser(
        str(self.config.get("kobo_backup.directory", "") or "").strip()
    ))
    if configured:
        backup_root = Path(configured)
        if not backup_root.is_absolute():
            backup_root = library_root / backup_root
    else:
        backup_root = library_root
    identity = self.identity_by_id(identity_id)
    directory_name = identity.directory_name if identity and identity.directory_name else identity_id
    target_dir = backup_root / safe_library_component(directory_name, "default") / "kobo"
    return library_root, target_dir


def _kobo_manifest_rejection_count(session: Any) -> int:
    """Sanitized count of provider-discarded source rows; no plugin imports."""
    raw = getattr(session, "backup_manifest_rejection_count", 0)
    return raw if type(raw) is int and 0 <= raw <= 1_000_000 else 0


_KOBO_CHECKPOINT_STATUSES = frozenset({
    "complete", "partial", "failed", "aborted", "cancelled",
    "attention_required", "running", "interrupted", "manifest_fetch",
    "skipped_fresh",
})


def _kobo_safe_checkpoint_status(value: Any) -> str:
    """Accept only internal checkpoint states, never free provider strings."""
    return value if type(value) is str and value in _KOBO_CHECKPOINT_STATUSES else "unknown"


def _kobo_checkpoint_number(value: Any, *, limit: int = 1_000_000) -> int:
    """Fail closed on malformed local SQLite metadata rather than raising."""
    return value if type(value) is int and 0 <= value <= limit else 0



def _kobo_manifest_flags_invalid(raw: dict[str, Any]) -> bool:
    """Reject explicit ambiguous permission flags in an owned-book manifest.

    Only boolean JSON flags are admissible. A personal-library manifest may
    omit ownership/downloadability (Vault legacy contract); an explicitly
    false ownership claim or malformed flag can NEVER authorize acquisition.
    """
    if raw.get("owned") is False:
        return True
    for key in ("owned", "downloadable", "archived"):
        value = raw.get(key)
        if value is not None and type(value) is not bool:
            return True
    return False


def kobo_backup_preview(
    self, credential_id: str, *,
    refresh_manifest: bool = False, verify_local_files: bool = False,
    _manifest_evidence: tuple[list, int] | None = None,
    _item_reasons: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Read-only preflight for one Kobo source; never grants acquisition.

    Without refresh_manifest the projection may omit recent purchases or
    revocations. An explicit manifest refresh requests *rights only* from an
    already connected provider, never content URLs or local materialization.

    The returned list contains only opaque item digests and reason codes,
    neither book titles, raw product IDs, credentials nor signed URLs. It is
    advisory: a subsequent acquisition MUST revalidate manifest and files.
    """
    from .library_declared_links import DeclarationRejected, has_active_declared_link

    credential_id = str(credential_id or "").strip()
    identity_id = self.storage.identity_for_connection("kobo", credential_id)
    if not credential_id or not identity_id:
        raise ProviderConfigurationError("Compte Kobo demandé non configuré")

    session = next((
        value for value in self._sessions.values()
        if value.provider_id == "kobo" and value.credential_id == credential_id
        and value.connected and getattr(value.capabilities, "backup", False)
    ), None)
    if refresh_manifest and session is None:
        raise ProviderConfigurationError(
            "Compte Kobo non connecté : impossible de rafraîchir les droits"
        )

    # A fresh remote manifest must remain attached to this *same* Core
    # account/session throughout the subsequent local projection. The
    # offline checkpoint preview is intentionally still usable disconnected.
    core_epoch = source_context_epoch(self)
    provider_epoch = getattr(session, "_epoch", None)
    # Multiple borrowers are legitimate diagnostic inputs: the existing
    # preview must classify their rights as attention_required rather than
    # convert an ambiguous account into an authoritative owner. Pin every
    # observed instance/value so mid-read replacement is still rejected.
    accounts = tuple(session.accounts) if session is not None else ()
    account_scopes = tuple(
        (row.identity_id, row.provider_id, row.credential_id, row.unique_id)
        for row in accounts
    )

    def fresh_source_current() -> bool:
        try:
            return bool(
                session is not None
                and session.connected is True
                and source_context_epoch(self) == core_epoch
                and self._sessions.get(self._session_key("kobo", credential_id)) is session
                and getattr(session, "_epoch", None) == provider_epoch
                and len(session.accounts) == len(accounts)
                and all(
                    live is original
                    for live, original in zip(session.accounts, accounts)
                )
                and tuple(
                    (row.identity_id, row.provider_id,
                     row.credential_id, row.unique_id)
                    for row in session.accounts
                ) == account_scopes
                and (
                    len(accounts) != 1 or account_scopes[0][:3]
                    == (identity_id, "kobo", credential_id)
                )
                and self.storage.identity_for_connection(
                    "kobo", credential_id,
                ) == identity_id
            )
        except Exception:
            return False

    if refresh_manifest and not fresh_source_current():
        raise ProviderConfigurationError("Source Kobo modifiée pendant l’aperçu")

    stored = self.storage.backup_list("kobo", credential_id)
    previous = {str(row.get("item_id") or ""): row for row in stored}
    if _manifest_evidence is not None:
        manifest, upstream_rejected = _manifest_evidence
        refresh_manifest = True
    elif refresh_manifest:
        manifest = session.backup_manifest()
        if not isinstance(manifest, list):
            raise ProviderConfigurationError("Manifeste Kobo non conforme")
        upstream_rejected = _kobo_manifest_rejection_count(session)
    else:
        upstream_rejected = 0
        # A local projection is never an authoritative rights manifest.
        manifest = [
            {"product_id": row["item_id"], "revision_id": row.get("revision_id"),
             "title": row.get("title"), "author": row.get("author"),
             "format": row.get("format")}
            for row in stored
        ]

    library_root, target_dir = _kobo_preview_paths(self, identity_id)
    indexed = _indexed_kobo_catalog_paths(self)
    source_digest = _kobo_checkpoint_key(identity_id, credential_id).rsplit(".", 1)[-1]
    checkpoint = _kobo_checkpoint_for(self, identity_id, credential_id)
    unlinked_local_books = self.kobo_unlinked_local_count(identity_id)
    counts = {
        "already_local_linked": 0, "already_local_verified": 0,
        "needs_reconciliation": 0, "attention_required": 0, "skipped_archived": 0,
        "blocked_scope": 0, "blocked_unlinked": 0, "not_downloadable": 0, "candidate_unverified": 0,
        "potentially_missing": 0, "invalid_manifest_items": upstream_rejected,
    }
    # Do not change the historical offline/legacy counter schema. A
    # declaration-presence result is exposed only by a caller that explicitly
    # refreshed its manifest AND requested a physical file check.
    if refresh_manifest and verify_local_files:
        counts["already_local_declared"] = 0
    examples: list[dict[str, str]] = []
    seen: set[str] = set()
    # A verified declaration is local presence, not an authenticated Kobo
    # receipt. Keep one bounded physical-check budget for the entire preview.
    presence_seconds_remaining = 8.0

    for raw in manifest:
        if refresh_manifest and not fresh_source_current():
            raise ProviderConfigurationError("Source Kobo modifiée pendant l’aperçu")
        if not isinstance(raw, dict):
            counts["invalid_manifest_items"] += 1
            continue
        product_id = str(raw.get("product_id") or raw.get("id") or "").strip()
        if not product_id or product_id in seen:
            counts["invalid_manifest_items"] += 1
            continue
        seen.add(product_id)
        if refresh_manifest and _kobo_manifest_flags_invalid(raw):
            # A preview must not suggest "potentially missing" when the
            # source explicitly denies ownership or has invalid rights flags.
            counts["invalid_manifest_items"] += 1
            continue
        current = previous.get(product_id)
        revision_id = str(raw.get("revision_id") or "").strip() or None
        identity_item_key = (identity_id, product_id)
        record = {
            "item_id": product_id,
            "title": raw.get("title") or (current or {}).get("title"),
            "author": _manifest_author(raw) or (current or {}).get("author"),
        }

        reason = "skipped_archived" if _manifest_archived(raw, current) else ""
        path_raw = str((current or {}).get("local_path") or "").strip()
        if path_raw and not reason:
            linked = Path(path_raw)
            if linked.is_symlink():
                reason = "needs_reconciliation"
            elif linked.is_file():
                if (
                    current and current.get("status") in {"ok", "complete"}
                    and (not refresh_manifest or current.get("revision_id") == revision_id)
                ):
                    if verify_local_files:
                        try:
                            observed = _observed_backup_artifact(linked)
                        except (ProviderConfigurationError, OSError):
                            reason = "needs_reconciliation"
                        else:
                            expected_hash = str(current.get("sha256") or "")
                            expected_size = current.get("size")
                            if (expected_hash and observed["sha256"] == expected_hash
                                    and (expected_size is None or observed["size"] == expected_size)):
                                reason = "already_local_verified"
                            else:
                                reason = "needs_reconciliation"
                    else:
                        reason = "already_local_linked"
                else:
                    reason = "needs_reconciliation"
            else:
                # A previously linked but now missing placement is not proof
                # that a new file should be downloaded with another name.
                reason = "needs_reconciliation"

        if not reason and not path_raw and current and current.get("status") in {"ok", "complete"}:
            # An earlier completed acquisition without a recorded placement
            # is inconsistent provenance, not proof that no file was written.
            reason = "needs_reconciliation"

        if not reason:
            candidate_paths = _kobo_record_candidate_paths(self, record, target_dir)
            for explicit in indexed.get(identity_item_key, ()):
                if explicit not in candidate_paths:
                    candidate_paths.append(explicit)
            if library_root != target_dir:
                for legacy in _kobo_record_candidate_paths(self, record, library_root):
                    if legacy not in candidate_paths:
                        candidate_paths.append(legacy)
            if candidate_paths or (
                current and current.get("status") in {"reconcile_required", "reconcile_conflict"}
            ):
                reason = "needs_reconciliation"

        if not reason:
            # A private declaration blocks a candidate acquisition, but is
            # never classified as a verified Kobo backup. Resolve the borrower
            # from the live provider for a refreshed manifest; do not trust a
            # backup row belonging to a previous login as the current owner.
            account_id = None
            if session is not None and len(session.accounts) == 1:
                owner = session.accounts[0]
                if owner.identity_id == identity_id:
                    account_id = owner.unique_id
            elif session is None and current and current.get("identity_id") == identity_id:
                account_id = current.get("account_id")
            if type(account_id) is str and account_id:
                try:
                    if has_active_declared_link(
                        self.storage, identity_id=identity_id,
                        provider_id="kobo", credential_id=credential_id,
                        account_id=account_id, item_id=product_id,
                    ):
                        reason = "attention_required"
                        # Only an explicit, fresh-file preview can recognize
                        # physical presence. Mere recording never proves
                        # the EPUB exists or authorizes an acquisition.
                        if (verify_local_files and refresh_manifest
                                and session is not None
                                and presence_seconds_remaining > 0):
                            from .library_declared_presence import verify_declared_presence
                            storage_at_start = self.storage

                            def source_current() -> bool:
                                return bool(
                                    self.storage is storage_at_start
                                    and session.connected
                                    and any(value is session for value in self._sessions.values())
                                    and len(session.accounts) == 1
                                    and session.accounts[0].identity_id == identity_id
                                    and session.accounts[0].unique_id == account_id
                                    and self.storage.identity_for_connection(
                                        "kobo", credential_id) == identity_id
                                )

                            checked_started = time.monotonic()
                            try:
                                presence = verify_declared_presence(
                                    self, identity_id=identity_id,
                                    provider_id="kobo", credential_id=credential_id,
                                    account_id=account_id, item_id=product_id,
                                    source_current=source_current,
                                    seconds=presence_seconds_remaining,
                                )
                            finally:
                                presence_seconds_remaining = max(
                                    0.0, presence_seconds_remaining
                                    - max(0.0, time.monotonic() - checked_started),
                                )
                            if presence.status == "declared_current":
                                reason = "already_local_declared"
                except DeclarationRejected:
                    # Malformed user statements cannot authorize a fresh GET.
                    reason = "attention_required"
            elif refresh_manifest:
                # A fresh manifest without a unique account is not evidence
                # that a particular user's file/declaration is absent.
                reason = "attention_required"

        if not reason:
            if bool(raw.get("archived")):
                reason = "skipped_archived"
            elif raw.get("downloadable") is False:
                reason = "not_downloadable"
            elif (current and current.get("status") == "attention_required"
                  and current.get("attention_code") not in {
                      "declared_link_recorded", "declared_links_unavailable", "archived_book",
                  }):
                # The previous declaration-only attention is recalculated
                # above on every preview. A revoked statement must not remain
                # a permanent non-downloadable status.
                reason = (
                    "blocked_scope"
                    if current.get("attention_code") == "missing_delegation_scope"
                    else "attention_required"
                )
            elif not refresh_manifest:
                reason = "candidate_unverified"
            else:
                # No descriptor has been resolved and no file was downloaded:
                # this is NOT a promise that the provider can deliver a file.
                reason = "potentially_missing"

        counts[reason] += 1
        if _item_reasons is not None:
            _item_reasons[product_id] = reason
        if len(examples) < 20 and reason not in {
            "already_local_linked", "already_local_verified",
            "already_local_declared",
        }:
            item_digest = hashlib.sha256(
                (credential_id + "\\x00" + product_id).encode("utf-8")
            ).hexdigest()[:20]
            examples.append({"item_ref": item_digest, "reason": reason})

    # Do not publish stale positive local presence after a concurrent
    # account rebind, reconnect or consent/session transition during SHA
    # verification or declaration checking.
    if refresh_manifest and not fresh_source_current():
        raise ProviderConfigurationError("Source Kobo modifiée pendant l’aperçu")
    # Explicitly discourage treating the output as an executable download
    # plan. A complete fresh manifest is still only a rights snapshot.
    return {
        "scope": "account",
        "source_id": source_digest,
        "manifest_refreshed": bool(refresh_manifest),
        "manifest_items": len(manifest),
        "known_item_count": len(seen),
        "provider_rejected_manifest_items": upstream_rejected,
        "checkpoint_status": _kobo_safe_checkpoint_status(
            (checkpoint or {}).get("status")
        ),
        "unlinked_local_books": unlinked_local_books,
        "counts": counts,
        "blocking_items": sum(counts[key] for key in (
            "needs_reconciliation", "attention_required",
            "blocked_scope", "blocked_unlinked", "not_downloadable",
            "invalid_manifest_items",
        )),
        "examples": examples,
        "preview_only": True,
        "acquisition_authorized": False,
        "requires_manifest_validation": not refresh_manifest,
        "manifest_valid": counts["invalid_manifest_items"] == 0,
        "verification": "artifact_hash" if verify_local_files else "linked_path_only",
    }



def _safe_kobo_opening_decision(raw: Any) -> dict[str, Any] | None:
    """Strictly filter the durable opening decision before UI/CLI export.

    The database value may have been altered by an old installation or a
    plugin. Do not forward arbitrary keys, messages, identifiers or URLs.
    """
    if not isinstance(raw, dict) or type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        return None
    allowed = {
        "disabled", "no_session", "capability_unavailable", "source_disabled",
        "fresh_cache", "started", "partial", "error", "manual_only",
    }
    status = raw.get("status")
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": status if type(status) is str and status in allowed else "unknown",
        "persisted_enabled": raw.get("persisted_enabled") is True,
    }
    for key in ("connected_kobo", "eligible_kobo", "scheduled_kobo"):
        value = raw.get(key)
        if type(value) is int and 0 <= value <= 1_000_000:
            result[key] = value
    checked = raw.get("checked_at")
    if type(checked) is int and 0 <= checked <= 4_102_444_800:
        result["checked_at"] = checked
    return result


def kobo_backup_diagnostic(self) -> dict[str, Any]:
    """Summarise the persisted Kobo backup state without network access."""
    rows = self.storage.backup_list(provider_id="kobo")
    try:
        opening_raw = json.loads(
            self.storage.metadata_get("kobo_backup_opening_decision_v1") or "null"
        )
    except (TypeError, ValueError):
        opening_raw = None
    opening_decision = _safe_kobo_opening_decision(opening_raw)
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
        # Aggregate permissions without exposing book metadata or bearer data.
        "items_blocked_by_scope": sum(
            1 for row in rows
            if row.get("status") == "attention_required"
            and row.get("attention_code") == "missing_delegation_scope"
        ),
        "statuses": dict(statuses),
        "opening_decision": opening_decision,
        "sources": _kobo_source_summaries(self),
        "error_summary": [
            {"message": message, "count": count}
            for message, count in errors.most_common(10)
        ],
        "error_examples": examples,
        "reconcile_examples": reconcile_examples,
        "attention_examples": attention_examples,
        "storage_schema_version": self.storage.schema_version(),
    }




def safe_kobo_backup_report(report: dict[str, Any]) -> dict[str, Any]:
    """Create a shareable *persisted* Kobo report without personal data.

    The internal kobo_backup_diagnostic is deliberately rich for the local UI
    and reconciliation tools. Never print that raw dictionary from CLI:
    provider messages can include signed URLs, filenames and bearer values,
    and the backup rows contain private titles, paths and product IDs.
    """
    known_item_statuses = {
        "ok", "complete", "error", "pending", "pending_acquisition",
        "acquiring", "attention_required", "reconcile_required",
        "reconcile_conflict", "archived_book", "skipped_archived", "unlinked_local_books",
        "missing", "remote_owned", "unknown",
    }
    known_source_statuses = {
        "complete", "partial", "failed", "aborted", "cancelled",
        "attention_required", "running", "interrupted", "manifest_fetch",
        "skipped_fresh",
    }
    source_metrics = {
        "position", "total", "updated_at", "failure_streak", "scanned",
        "attempted", "downloaded", "unchanged", "pending",
        "attention_required", "errors", "skipped", "skipped_archived", "remaining",
        "reconcile_pending", "reconcile_skipped", "items_blocked_by_scope",
        "malformed_manifest_items", "duplicate_manifest_items",
        "provider_rejected_manifest_items",
    }

    def count(value: Any) -> int:
        # Boolean is a subclass of int: reject it, as well as untrusted
        # provider strings, floats, negatives and oversized numbers.
        return value if type(value) is int and 0 <= value <= 1_000_000 else 0

    statuses: dict[str, int] = {}
    raw_statuses = report.get("statuses")
    if isinstance(raw_statuses, dict):
        for raw_status, raw_count in raw_statuses.items():
            value = count(raw_count)
            status = (
                raw_status if type(raw_status) is str and raw_status in known_item_statuses
                else "other"
            )
            statuses[status] = min(1_000_000, statuses.get(status, 0) + value)

    sources: list[dict[str, Any]] = []
    raw_sources = report.get("sources")
    if isinstance(raw_sources, list):
        for source in raw_sources[:25]:
            if not isinstance(source, dict):
                continue
            state = source.get("status")
            item: dict[str, Any] = {
                "source_index": len(sources) + 1,
                "status": (
                    state if type(state) is str and state in known_source_statuses
                    else "unknown"
                ),
            }
            source_id = source.get("source_id")
            # The checkpoint key contains a 24-character SHA-256 digest, not
            # a raw Kobo credential, identity, product ID or account name.
            if isinstance(source_id, str) and re.fullmatch(r"[0-9a-f]{24}", source_id):
                item["source_id"] = source_id
            for key in source_metrics:
                value = source.get(key)
                # A Unix timestamp exceeds 1 million seconds, unlike normal
                # item counters. Reject future/invalid dates independently.
                limit = (
                    4_102_444_800 if key == "updated_at" else
                    6 if key == "failure_streak" else 1_000_000
                )
                if type(value) is int and 0 <= value <= limit:
                    item[key] = value
            sources.append(item)

    return {
        "redacted": True,
        "items": count(report.get("items")),
        "items_blocked_by_scope": count(report.get("items_blocked_by_scope")),
        "statuses": statuses,
        "error_count": statuses.get("error", 0),
        "reconcile_count": (
            statuses.get("reconcile_required", 0)
            + statuses.get("reconcile_conflict", 0)
        ),
        "attention_count": statuses.get("attention_required", 0),
        "opening_decision": _safe_kobo_opening_decision(report.get("opening_decision")),
        "sources": sources,
    }


def _kobo_checkpoint_for(self, identity_id: str, credential_id: str) -> dict[str, Any] | None:
    """One established v1 checkpoint contract, no second checkpoint store."""
    try:
        row = json.loads(
            self.storage.metadata_get(_kobo_checkpoint_key(identity_id, credential_id)) or "null"
        )
    except (TypeError, ValueError):
        return None
    if not isinstance(row, dict) or row.get("schema_version") != 1:
        return None
    return row


def _kobo_source_summaries(self) -> list[dict[str, Any]]:
    """Expose only safe checkpoint counters for the local diagnostic."""
    summaries = []
    # kobo-sync-errors must work after a cold restart without loading or
    # connecting provider plugins. Sessions are transient; the connection
    # registry and checkpoint keys are durable in the local SQLite database.
    scopes = {
        (str(row.get("identity_id") or ""), str(row.get("credential_id") or ""))
        for row in self.storage.provider_connections()
        if row.get("provider_id") == "kobo"
        and row.get("identity_id") and row.get("credential_id")
    }
    # Retain compatibility with tests/legacy in-memory sessions that have not
    # yet been inserted in provider_connections.
    for session in self._sessions.values():
        if session.provider_id == "kobo":
            identity = self.identity_for_connection("kobo", session.credential_id).identity_id
            scopes.add((identity, session.credential_id))
    for identity, credential_id in sorted(scopes):
        source_id = _kobo_checkpoint_key(identity, credential_id).rsplit(".", 1)[-1]
        row = _kobo_checkpoint_for(self, identity, credential_id)
        if row is None:
            continue
        counts = row.get("counts") if isinstance(row.get("counts"), dict) else {}
        safe_counts = {
            key: int(value) for key, value in counts.items()
            if key in {"scanned", "attempted", "downloaded", "unchanged",
                       "pending", "attention_required", "errors", "skipped",
                       "skipped_archived", "remaining", "reconcile_pending", "reconcile_skipped",
                       "items_blocked_by_scope", "malformed_manifest_items",
                       "duplicate_manifest_items",
                       "provider_rejected_manifest_items"}
            and type(value) is int and 0 <= value <= 1000000
        }
        raw_streak = row.get("failure_streak")
        failure_streak = (
            raw_streak if type(raw_streak) is int and 0 <= raw_streak <= 6 else 0
        )
        total = _kobo_checkpoint_number(row.get("total"))
        summaries.append({
            "source_id": source_id,
            "status": _kobo_safe_checkpoint_status(row.get("status")),
            "position": min(_kobo_checkpoint_number(row.get("processed")), total),
            "total": total,
            "updated_at": _kobo_checkpoint_number(
                row.get("updated_at"), limit=4_102_444_800,
            ),
            # Public diagnostics: safe numeric cooldown, no raw credential.
            "failure_streak": failure_streak,
            **safe_counts,
        })
    summaries.sort(key=lambda row: row["source_id"])
    return summaries

def _kobo_backup_due(self, *, credential_id: str | None = None) -> bool:
    try:
        interval_minutes = max(1, int(self.config.get("kobo_backup.interval_minutes", 60) or 60))
    except (TypeError, ValueError):
        interval_minutes = 60
    # A new account cannot inherit another account's fresh checkpoint.
    raw = None
    if credential_id is not None:
        identity = self.identity_for_connection("kobo", credential_id).identity_id
        row = _kobo_checkpoint_for(self, identity, credential_id)
        if row is not None:
            # Neither user cancellation nor a blocked acquisition awaiting
            # attention is a transient failure. Automatic restarts cannot fix
            # consent, missing permissions or local reconciliation; an explicit
            # source-scoped manual operation is required to resume.
            if str(row.get("status") or "") in {"cancelled", "attention_required"}:
                return False
            # An incomplete checkpoint after a crash is never a successful cache.
            if str(row.get("status") or "") in {"running", "interrupted", "manifest_fetch"}:
                return True
            # Exponential, bounded cooldown for repeated source errors.
            # Only the automatic scheduler observes this; an explicit account
            # sync may retry immediately after recovery or reauthorization.
            if str(row.get("status") or "") in {"failed", "partial", "aborted"}:
                streak = row.get("failure_streak")
                if type(streak) is int and streak > 1:
                    multiplier = 1 << min(streak - 1, 5)
                    interval_minutes = min(
                        max(interval_minutes, 24 * 60), interval_minutes * multiplier,
                    )
            raw = row.get("updated_at")
        else:
            # Preserve old cache semantics only before *any* active account
            # has a v1 checkpoint. Existing accounts must never mask new ones.
            any_checkpoint = any(
                _kobo_checkpoint_for(
                    self, self.identity_for_connection("kobo", session.credential_id).identity_id,
                    session.credential_id,
                ) is not None
                for session in self._sessions.values() if session.provider_id == "kobo"
            )
            if any_checkpoint:
                return True
    if raw is None:
        raw = (self.storage.metadata_get("kobo_backup_last_attempt_at")
               or self.storage.metadata_get("kobo_backup_last_success_at"))
    try:
        last = int(raw or 0)
    except (TypeError, ValueError):
        last = 0
    now = int(time.time())
    # NTP corrections / restored backups may leave a last_attempt timestamp
    # far in the future. Without this guard the automatic scheduler would
    # falsely claim "fresh_cache" for hours or days after the clock returns
    # to normal. Explicit cancellation was handled above and stays blocked.
    if last > now + 5 * 60:
        return True
    return last <= 0 or (now - last) >= interval_minutes * 60


def sync_kobo_backup(self, progress: Callable[[dict[str, Any]], None] | None=None, *, credential_id: str | None=None, retry_scope_blocked: bool=False, cancelled: Callable[[], bool] | None=None, lock_factory, lock_path) -> dict[str, Any]:
    """Compatibility entry point for the Kobo local synchronization.

    0.8.0 keeps the validated cross-process serialization for acquisition work. The
    same global lock is intentionally named for local acquisition rather
    than Kobo so the 0.8.x loan synchronizer can share it.
    """
    lock = lock_factory(lock_path())
    try:
        with lock:
            if credential_id is None:
                if retry_scope_blocked:
                    raise ProviderConfigurationError(
                        "Le réessai de délégation Kobo exige un compte explicite"
                    )
                if cancelled is not None:
                    raise ProviderConfigurationError(
                        "Annulation Kobo : compte explicite requis"
                    )
                return self._sync_kobo_backup_locked(progress=progress)
            return self._sync_kobo_backup_account_locked(
                credential_id, progress=progress, cancelled=cancelled,
                retry_scope_blocked=retry_scope_blocked,
            )
    except LockBusyError as exc:
        raise ProviderConfigurationError(
            "Une synchronisation locale est déjà en cours dans une autre instance de cifereca"
        ) from exc


def _attested_received_kobo_copy(
    row: dict[str, Any], path: Path, *,
    identity_id: str, placements: dict[str, dict[str, Any]],
) -> bool:
    """A copied local file is not an *orphan Kobo acquisition* of recipient B.

    Trust neither the catalogue label nor a filename alone: the recipient's
    own persisted placement must corroborate the exact Core transfer markers.
    This exception affects only the global unknown-books counter; a matching
    supplier product still needs per-item reconciliation, never an implicit
    Kobo entitlement/backup.
    """
    metadata = row.get("metadata")
    if (row.get("provider_id") != "kobo" or type(metadata) is not dict
            or type(identity_id) is not str or not identity_id
            or not path.is_absolute() or path.is_symlink()
            or path.resolve() != path):
        return False
    placement_id = row.get("placement_id")
    placement = placements.get(placement_id) if type(placement_id) is str else None
    if (type(placement) is not dict
            or placement.get("state") != "present"
            or placement.get("identity_id") != identity_id
            or placement.get("provider_id") != "kobo"
            or str(placement.get("source_item_id") or "") != str(row.get("item_id") or "")
            or str(placement.get("artifact_id") or "") != str(row.get("artifact_id") or "")
            or placement.get("path") != str(path)):
        return False
    placement_metadata = placement.get("metadata")
    if type(placement_metadata) is not dict:
        return False
    origin = metadata.get("file_origin_identity_id")
    from_placement = metadata.get("materialized_from")
    if (type(origin) is not str or not origin or origin == identity_id
            or type(from_placement) is not str or not from_placement):
        return False
    return all(
        metadata.get(field) == placement_metadata.get(field) == value
        for field, value in (
            ("transfer_kind", "local_copy"),
            ("file_origin_identity_id", origin),
            ("materialized_from", from_placement),
        )
    )


def kobo_unlinked_local_count(self, identity_id: str) -> int:
    """Block true unlinked local books, not copies received from another ID.

    Cross-identity copies are neither Kobo B rights nor backup receipts:
    if B's manifest contains that product, _indexed_kobo_catalog_paths still
    blocks *that item* for explicit reconciliation, not the entire batch.
    """
    known = {str(Path(r["local_path"]).resolve()) for r in self.storage.backup_list("kobo")
             if r.get("identity_id") == identity_id and r.get("local_path")}
    default_id = self.identities[0].identity_id if self.identities else None
    placements = {
        str(row.get("placement_id") or ""): row
        for row in self.storage.placements(state="present")
        if row.get("identity_id") == identity_id
    }
    paths = set()
    for row in self.storage.catalog_entries():
        owner = row.get("identity_id") or default_id
        raw = (row.get("metadata") or {}).get("path")
        if (owner == identity_id and row.get("local_state") == "local"
                and str(row.get("provider_id") or "local") in {"local", "kobo"} and raw):
            path = Path(raw)
            if path.is_file() and str(path.resolve()) not in known:
                if _attested_received_kobo_copy(
                    row, path, identity_id=identity_id, placements=placements,
                ):
                    continue
                paths.add(str(path.resolve()))
    return len(paths)


def _sync_kobo_backup_locked(self, progress: Callable[[dict[str, Any]], None] | None=None, *, logger, credential_id: str | None = None, cancelled: Callable[[], bool] | None = None, retry_scope_blocked: bool = False, confirmed_manifest: list | None = None, source_guard: Callable[[], None] | None = None, item_guard: Callable[[SearchResult, str, str | None], None] | None = None) -> dict[str, Any]:
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
    # The confirmed broker path already refreshed/validated this exact manifest
    # under the acquisition lock. Never broaden it with another provider read,
    # a global catalogue scan, remnant recovery or authoritative pruning.
    confirmed = confirmed_manifest is not None
    if confirmed:
        if credential_id is None or source_guard is None or item_guard is None:
            raise ProviderConfigurationError("Confirmation de source invalide")
        source_guard()
    cancellation_latched = False

    def cancellation_requested() -> bool:
        nonlocal cancellation_latched
        if cancellation_latched:
            return True
        if cancelled is None:
            return False
        try:
            cancellation_latched = bool(cancelled())
        except Exception:
            # A broken cancellation hook must not launch another acquisition
            # or emit arbitrary plugin-controlled exception class names.
            logger.warning("TRACE kobo cancellation hook failed code=callback_failed")
            cancellation_latched = True
        return cancellation_latched

    def emit(event: str, **detail: Any) -> None:
        if progress is None:
            return
        try:
            progress({"event": event, **detail})
        except Exception:
            # Progress reporting must never break the backup itself.
            pass

    # A cancelled explicit resume is not a scan request. Avoid the expensive
    # local catalog/ADEPT probes entirely, and preserve existing artifacts.
    # The normal per-account loop still commits a durable cancelled checkpoint.
    cancelled_before_scan = cancellation_requested()
    if not cancelled_before_scan and not confirmed:
        self.catalog_scan()
        indexed_kobo_paths = _indexed_kobo_catalog_paths(self)
    else:
        indexed_kobo_paths = _indexed_kobo_catalog_paths(self) if confirmed and not cancelled_before_scan else {}
    sessions = [
        session for session in self._sessions.values()
        if session.connected and session.provider_id == "kobo" and session.capabilities.backup
        and (credential_id is None or session.credential_id == credential_id)
    ]
    if credential_id is not None and not sessions:
        raise ProviderConfigurationError("Compte Kobo demandé indisponible ou non connecté")
    if confirmed and len(sessions) != 1:
        raise ProviderConfigurationError("Confirmation de source invalide")
    target_dir = self._kobo_backup_target_dir(
        self.identity_for_connection("kobo", credential_id).identity_id
    ) if confirmed else self._kobo_backup_target_dir()
    target_dirs: dict[str, str] = {}
    preferred = str(self.config.get("kobo_backup.format", "epub") or "epub").lower()
    vault_versions = sorted({
        str(getattr(session, "api_version", "") or "2.x") for session in sessions
    })
    vault_contract = ",".join(vault_versions) if vault_versions else "2.x"
    if cancelled_before_scan:
        adept_state = {}
    else:
        emit("adept_probe", message="Vérification de Kobo-Adept")
        adept_state = self.kobo_adept_state(force=True)
    if not cancelled_before_scan:
        emit(
            "adept_ready",
            compatible=bool(adept_state.get("compatible")),
            operational=bool(adept_state.get("operational")),
            software_version=adept_state.get("software_version"),
            contract_version=adept_state.get("contract_version"),
        )
    adapter_available = bool(adept_state.get("available") and adept_state.get("compatible") and adept_state.get("operational"))
    summary: dict[str, Any] = {
        "scope": "account" if credential_id is not None else "all",
        "sessions": len(sessions), "manifests_fetched": 0, "manifest_items": 0,
        "scanned": 0, "malformed_manifest_items": 0, "duplicate_manifest_items": 0,
        "provider_rejected_manifest_items": 0, "attempted": 0, "pending": 0,
        "completed": 0, "downloaded": 0, "unchanged": 0,
        "already_local_declared": 0, "metadata_refreshed": 0,
        "skipped": 0, "skipped_archived": 0, "attention_required": 0, "errors": 0, "remaining": 0,
        "items_blocked_by_scope": 0, "attempts_saved": 0,
        "blocked_non_retryable": 0,
        "vault_errors": 0, "integration_errors": 0, "adept_errors": 0,
        "recovered_interrupted": 0, "recovered_unparsed": 0,
        "reconcile_pending": 0, "reconcile_conflicts": 0, "reconcile_skipped": 0,
        "legacy_pruned": 0, "legacy_local_preserved": 0, "legacy_entitlements_inactivated": 0,
        "aborted": False, "abort_reason": None,
        "cancelled": False, "cancelled_sources": 0,
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

    delegation_saved_before = {
        id(session): int(getattr(session, "_cifereca_delegation_attempts_saved", 0))
        for session in sessions
    }

    # Reconcile local remnants from interrupted / previously-unparseable
    # acquisitions before contacting Vault again. Never delete a file here:
    # one plausible artifact is quarantined for verification; several
    # plausible artifacts become an explicit conflict. This prevents the
    # historical ``Title [deadbeef].epub`` duplication loop.
    if not cancelled_before_scan and not confirmed:
        _recover_local_remnants(self, summary, target_dirs, emit, credential_id=credential_id)
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
    prunable_credentials: set[str] = set()
    source_metrics = (
        "scanned", "attempted", "downloaded", "unchanged",
        "already_local_declared", "pending", "skipped_archived",
        "attention_required", "errors", "remaining", "reconcile_pending",
        "reconcile_skipped", "items_blocked_by_scope", "blocked_non_retryable",
        "malformed_manifest_items", "duplicate_manifest_items",
        "provider_rejected_manifest_items",
    )
    summary["sources"] = []
    for session in sessions:
        session_identity = self.identity_for_connection("kobo", session.credential_id)
        initial = {name: int(summary.get(name, 0)) for name in source_metrics}
        previous_checkpoint = _kobo_checkpoint_for(
            self, session_identity.identity_id, session.credential_id,
        )
        previous_streak = (previous_checkpoint or {}).get("failure_streak")
        if type(previous_streak) is not int or previous_streak < 0:
            previous_streak = 0
        previous_streak = min(previous_streak, 6)
        source_total = 0
        processed = 0
        malformed = False
        committed_item_id: str | None = None

        def source_delta() -> dict[str, int]:
            return {name: int(summary.get(name, 0)) - initial[name] for name in source_metrics}

        def checkpoint(status: str) -> None:
            counts = source_delta()
            # In-progress writes must not erase the prior failure history.
            # A completed or attention-only batch resets the error streak;
            # an aborted/cancelled run never silently becomes 'complete'.
            if status in {"failed", "partial", "aborted"} and counts["errors"]:
                streak = min(6, previous_streak + 1)
            elif status in {"running", "interrupted", "manifest_fetch"}:
                streak = previous_streak
            else:
                streak = 0
            _store_kobo_checkpoint(
                self, session_identity.identity_id, session.credential_id,
                total=source_total, processed=processed, status=status,
                counts=counts, item_id=committed_item_id, failure_streak=streak,
            )

        # Stop even before reading a new account's remote manifest.
        if cancellation_requested():
            summary["cancelled"] = True
            summary["cancelled_sources"] += 1
            checkpoint("cancelled")
            delta = source_delta()
            summary["sources"].append({
                "identity_id": session_identity.identity_id,
                "credential_id": session.credential_id,
                "status": "cancelled", "manifest_items": 0, **delta,
            })
            # Preflight cancellation must have the same durable source
            # terminal notification as cancellation between manifest items.
            emit("source_done", credential_id=session.credential_id,
                 status="cancelled", manifest_items=0, **delta)
            emit("cancelled", current=0, total=0)
            abort_all = True
            break

        declared_budget = {"remaining": 8.0}
        session_target_dir = self._kobo_backup_target_dir(session_identity.identity_id)
        target_dirs[session_identity.identity_id] = str(session_target_dir)
        # Persist "manifest_fetch" before contacting the provider: if the
        # process dies during a slow manifest request, the previous fresh
        # "complete" checkpoint must not hide an interrupted synchronization.
        # A resumed run always revalidates the complete manifest and files.
        checkpoint("manifest_fetch")
        try:
            emit("manifest_fetch", credential_id=session.credential_id, message="Lecture du manifest Kobo-Vault")
            manifest = confirmed_manifest if confirmed else session.backup_manifest()
            source_total = len(manifest)
            checkpoint("running")
            summary["manifests_fetched"] += 1
            summary["manifest_items"] += len(manifest)
            upstream_rejected = _kobo_manifest_rejection_count(session)
            if upstream_rejected:
                # A valid *subset* must never be considered an authoritative
                # complete manifest: no removal of historical Kobo rights.
                malformed = True
                summary["provider_rejected_manifest_items"] += upstream_rejected
                summary["malformed_manifest_items"] += upstream_rejected
            manifest_product_counts = Counter(
                str(raw.get("product_id") or raw.get("id") or "").strip()
                for raw in manifest if isinstance(raw, dict)
                if str(raw.get("product_id") or raw.get("id") or "").strip()
            )
            # A repeated product ID may be two conflicting revisions. Identify
            # every conflicting row BEFORE any acquisition, never download the
            # first variant only to overwrite it with the second.
            ambiguous_product_ids = {
                product_id for product_id, count in manifest_product_counts.items()
                if count > 1
            }
            authoritative_manifest_ids[session.credential_id] = set(manifest_product_counts)
            emit("manifest", credential_id=session.credential_id, total=len(manifest))
        except Exception as exc:
            summary["errors"] += 1
            info = record_error(exc, title=session.label)
            if info["fatal"]:
                summary["aborted"] = True
                summary["abort_reason"] = info
                abort_all = True
            terminal_status = "aborted" if abort_all else "failed"
            checkpoint(terminal_status)
            delta = source_delta()
            summary["sources"].append({
                "identity_id": session_identity.identity_id,
                "credential_id": session.credential_id,
                "status": terminal_status,
                "manifest_items": 0, **delta,
            })
            # Manifest failure is terminal for this source too. Publish only
            # after the durable checkpoint, exactly like item-loop endings.
            emit("source_done", credential_id=session.credential_id,
                 status=terminal_status, manifest_items=0, **delta)
            if abort_all:
                break
            continue

        for index, raw in enumerate(manifest):
            if source_guard is not None:
                source_guard()
            # Cooperative cancellation is checked strictly BETWEEN items:
            # never abandon an in-flight content write or mark it complete
            # without its durable backup row.
            if cancellation_requested():
                summary["cancelled"] = True
                summary["cancelled_sources"] += 1
                summary["remaining"] += len(manifest) - index
                checkpoint("cancelled")
                emit("cancelled", current=index, total=len(manifest),
                     remaining=len(manifest) - index)
                abort_all = True
                break

            item_committed = False
            # "downloaded", "item_error" and reconciliation are durable item
            # results, unlike the preliminary "scan" notification. Keep them
            # private until the per-item SQLite checkpoint has been committed.
            item_events: list[tuple[str, dict[str, Any]]] = []

            def defer_item_event(event: str, **detail: Any) -> None:
                item_events.append((event, detail))

            try:
                if not isinstance(raw, dict):
                    malformed = True
                    summary["malformed_manifest_items"] += 1
                    summary["skipped"] += 1
                    item_committed = True
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
                    malformed = True
                    summary["malformed_manifest_items"] += 1
                    summary["skipped"] += 1
                    item_committed = True
                    continue
                if product_id in ambiguous_product_ids:
                    # Do not trust either version of a duplicated product:
                    # keep existing entitlements/artifacts and quarantine the
                    # batch from authoritative prune until Vault is repaired.
                    malformed = True
                    summary["malformed_manifest_items"] += 1
                    summary["duplicate_manifest_items"] += 1
                    summary["skipped"] += 1
                    item_committed = True
                    continue
                if _kobo_manifest_flags_invalid(raw):
                    # Fail closed before even touching an existing backup row.
                    # An ambiguous rights manifest is never prune authority.
                    malformed = True
                    summary["malformed_manifest_items"] += 1
                    summary["skipped"] += 1
                    item_committed = True
                    continue
                content_type = str(raw.get("content_type") or "ebook").lower()
                if content_type not in {"ebook", "book", "epub"}:
                    summary["skipped"] += 1
                    item_committed = True
                    continue
                item = _refresh_manifest_record(self, session, session_identity, raw, product_id, summary)
                if _skip_manifest_item(
                    self, item, raw, session_target_dir, indexed_kobo_paths,
                    adapter_available, adept_state,
                    index, len(manifest), summary, defer_item_event,
                    retry_scope_blocked=retry_scope_blocked,
                    session=session, declared_budget=declared_budget,
                ):
                    item_committed = True
                    continue

                if source_guard is not None:
                    source_guard()
                if _acquire_manifest_item(self, session, session_identity, item, raw, session_target_dir, index, len(manifest), summary, defer_item_event, record_error, logger, item_guard=item_guard):
                    abort_all = True
                    item_committed = True
                    break
                item_committed = True
            finally:
                # Only advance past a committed item: exceptions must not
                # fabricate a resume checkpoint for an incomplete acquisition.
                if item_committed:
                    processed = index + 1
                    committed_item_id = str(raw.get("product_id") or raw.get("id") or "") if isinstance(raw, dict) else None
                checkpoint("running" if item_committed else "interrupted")
                # Checkpoint failure must propagate without announcing an
                # item as complete, blocked or failed. Incomplete items keep
                # an "interrupted" checkpoint but have no committed outcome.
                if item_committed:
                    for event, detail in item_events:
                        emit(event, **detail)
        delta = source_delta()
        if summary["cancelled"]:
            source_status = "cancelled"
        elif abort_all:
            source_status = "aborted"
        elif delta["errors"]:
            source_status = "partial" if delta["downloaded"] or delta["unchanged"] else "failed"
        elif malformed or delta["remaining"] or processed < source_total:
            source_status = "partial"
        elif any(delta[key] for key in ("pending", "attention_required", "reconcile_pending", "reconcile_skipped")):
            source_status = "attention_required"
        else:
            source_status = "complete"
        checkpoint(source_status)
        summary["sources"].append({
            "identity_id": session_identity.identity_id,
            "credential_id": session.credential_id,
            "status": source_status, "manifest_items": source_total, **delta,
        })
        # The source terminal event follows the durable terminal checkpoint.
        # Unlike a worker/task end, it reports the acquisition outcome for
        # this credential even if another credential remains in the batch.
        emit("source_done", credential_id=session.credential_id,
             status=source_status, manifest_items=source_total, **delta)
        if not malformed and not abort_all and processed == source_total and source_status in {"complete", "attention_required"}:
            prunable_credentials.add(session.credential_id)
        if abort_all:
            break

    # ``backup_items`` is a current-manifest compatibility projection.
    # Prune it only after the complete Kobo run succeeded: a failed/partial
    # sync must never make a remote title disappear merely because its
    # manifest could not be observed.  Artifacts/placements/catalog rows are
    # deliberately preserved by Storage.backup_prune_missing().
    if not confirmed:
        _prune_successful_manifest(self, summary, authoritative_manifest_ids, emit, prunable_credentials=prunable_credentials)
    summary["attempts_saved"] = sum(
        max(0, int(getattr(session, "_cifereca_delegation_attempts_saved", 0))
            - delegation_saved_before.get(id(session), 0))
        for session in sessions
    )
    return _publish_summary(self, summary, error_counts, error_examples, emit, scoped=credential_id is not None)
def _kobo_checkpoint_key(identity_id: str, credential_id: str) -> str:
    digest = hashlib.sha256(
        (identity_id + "\\x00" + credential_id).encode("utf-8")
    ).hexdigest()[:24]
    return "kobo_backup_checkpoint." + digest


def _store_kobo_checkpoint(
    self, identity_id: str, credential_id: str, *,
    total: int, processed: int, status: str,
    counts: dict[str, int], item_id: str | None = None,
    failure_streak: int = 0,
) -> None:
    """Record local progress; never use the index alone for skipping books."""
    item_digest = hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:16] if item_id else None
    self.storage.metadata_set(
        _kobo_checkpoint_key(identity_id, credential_id),
        json.dumps({
            "schema_version": 1, "total": total, "processed": processed,
            "status": status, "last_item_digest": item_digest,
            "counts": counts, "updated_at": int(time.time()),
            "failure_streak": min(6, max(0, int(failure_streak))),
        }, ensure_ascii=False, sort_keys=True),
    )


def _recover_local_remnants(self, summary, target_dirs, emit, *, credential_id: str | None = None):
    for previous in self.storage.backup_list("kobo"):
        if credential_id is not None and previous.get("credential_id") != credential_id:
            continue
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
        # The persisted path is a stronger clue than a generated filename:
        # users can rename/move an acquired file after an interrupted run.
        stored_path = str(previous.get("local_path") or "").strip()
        if stored_path:
            linked = Path(stored_path)
            if (linked.is_file() or linked.is_symlink()) and linked not in candidates:
                candidates.append(linked)
        if len(candidates) == 1:
            artifact = candidates[0]
            # Never dereference a symlink (including dangling links) when
            # recovering an unverified local artifact.
            ordinary_file = artifact.is_file() and not artifact.is_symlink()
            self.storage.backup_put({
                **previous,
                "status": "reconcile_required",
                "local_path": str(artifact),
                # Do not erase the original acquisition baseline when a
                # quarantined artifact is re-inspected on subsequent syncs.
                # Only an interrupted/unparsed acquisition without an
                # authoritative checksum may take a provisional observation.
                "size": (
                    previous.get("size")
                    if previous_status in {"reconcile_required", "reconcile_conflict"}
                    and previous.get("size") is not None
                    else artifact.stat().st_size if ordinary_file else None
                ),
                "sha256": (
                    previous.get("sha256")
                    if previous_status in {"reconcile_required", "reconcile_conflict"}
                    and previous.get("sha256")
                    else try_sha256_file(artifact) if ordinary_file else None
                ),
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
            # A missing candidate is NOT proof that the book is absent:
            # it may have been renamed or relocated outside scanned paths.
            # Keep the quarantine until an explicit reconciliation clears it.
            continue

    recovery_rows = [
        row for row in self.storage.backup_list("kobo")
        if credential_id is None or row.get("credential_id") == credential_id
    ]
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



def _prune_successful_manifest(self, summary, authoritative_manifest_ids, emit, *, prunable_credentials=None):
    # Preserve compatibility for callers without an explicit authority set.
    if prunable_credentials is None:
        prunable_credentials = (
            set(authoritative_manifest_ids)
            if not summary.get("errors") and not summary.get("aborted") else set()
        )
    for source_credential, seen_ids in authoritative_manifest_ids.items():
        if source_credential not in prunable_credentials:
            continue
        pruned = self.storage.backup_prune_missing("kobo", source_credential, seen_ids)
        summary["legacy_pruned"] += int(pruned.get("pruned") or 0)
        summary["legacy_local_preserved"] += int(pruned.get("local_preserved") or 0)
        summary["legacy_entitlements_inactivated"] += int(pruned.get("entitlements_inactivated") or 0)
        if pruned.get("pruned"):
            emit("legacy_pruned", credential_id=source_credential,
                 count=pruned.get("pruned"), local_preserved=pruned.get("local_preserved"))


_KOBO_DIAGNOSTIC_METRICS = frozenset({
    "sessions", "manifests_fetched", "manifest_items", "scanned", "attempted",
    "downloaded", "unchanged", "completed", "pending", "attention_required",
    "errors", "remaining", "skipped", "skipped_archived", "metadata_refreshed",
    "malformed_manifest_items", "duplicate_manifest_items",
    "provider_rejected_manifest_items", "reconcile_pending", "reconcile_conflicts", "reconcile_skipped",
    "items_blocked_by_scope", "blocked_non_retryable", "attempts_saved",
    "recovered_interrupted", "recovered_unparsed", "legacy_pruned",
    "legacy_local_preserved", "legacy_entitlements_inactivated",
    "vault_errors", "adept_errors", "integration_errors", "cancelled_sources",
})
_KOBO_DIAGNOSTIC_STATUSES = frozenset({
    "complete", "partial", "failed", "aborted", "cancelled",
    "attention_required", "skipped_fresh",
})


def _safe_kobo_backup_diagnostic_detail(summary: dict[str, Any]) -> dict[str, Any]:
    """Export only stable, non-identifying batch metrics.

    Diagnostics.text() may be shared externally. Its generic redactor cannot
    distinguish a Kobo book title, local path, identity ID or raw credential.
    Preserve the full summary *only* for the in-process caller/progress API.
    """
    def metric_fields(record: dict[str, Any]) -> dict[str, int]:
        return {
            key: value for key, value in record.items()
            if key in _KOBO_DIAGNOSTIC_METRICS
            and type(value) is int and 0 <= value <= 1_000_000
        }

    def safe_state(raw: Any) -> str:
        value = str(raw or "")
        return value if value in _KOBO_DIAGNOSTIC_STATUSES else "unknown"

    def safe_error(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        # Even error identifiers are untrusted plugin strings. Do not export
        # arbitrary messages, exception text, URLs or credential fragments.
        known_codes = {
            "unauthorized", "missing_token", "invalid_token", "invalid_bearer",
            "forbidden", "insufficient_scope", "invalid_api_token",
            "provider_http_error", "missing_delegation_scope",
            "no_compatible_http_candidate", "no_downloadable_artifact",
            "missing_pinned_delivery_capability", "adept_error",
            "result_unparseable_artifact_present", "archived_book",
            "network_error", "timeout", "rate_limited", "other",
        }
        raw_phase = str(raw.get("phase") or "")
        raw_code = str(raw.get("code") or "")
        record: dict[str, Any] = {
            "phase": raw_phase if raw_phase in {"vault", "adept", "integration"} else "other",
            "code": raw_code if raw_code in known_codes else "other",
        }
        status = raw.get("status_code")
        if type(status) is int and 100 <= status <= 599:
            record["status_code"] = status
        count = raw.get("count")
        if type(count) is int and 0 <= count <= 1_000_000:
            record["count"] = count
        return record

    result: dict[str, Any] = {
        "scope": "account" if summary.get("scope") == "account" else "all",
        "status": safe_state(summary.get("status")),
        "aborted": summary.get("aborted") is True,
        "cancelled": summary.get("cancelled") is True,
        **metric_fields(summary),
    }
    source_summaries = []
    if isinstance(summary.get("sources"), list):
        for index, source in enumerate(summary["sources"][:25], start=1):
            if not isinstance(source, dict):
                continue
            source_summaries.append({
                "source_index": index,
                "status": safe_state(source.get("status")),
                **metric_fields(source),
            })
    result["sources"] = source_summaries
    errors = summary.get("error_summary")
    result["error_summary"] = [
        parsed for raw in (errors[:10] if isinstance(errors, list) else [])
        if (parsed := safe_error(raw)) is not None
    ]
    abort = safe_error(summary.get("abort_reason"))
    if abort is not None:
        result["abort_reason"] = abort
    return result


def _publish_summary(self, summary, error_counts, error_examples, emit, *, scoped: bool = False):
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
    if summary.get("cancelled"):
        state = "cancelled"
    elif summary.get("aborted"):
        state = "aborted"
    elif summary.get("errors"):
        state = "partial" if summary.get("downloaded") or summary.get("unchanged") else "failed"
    elif summary.get("remaining") or summary.get("malformed_manifest_items"):
        state = "partial"
    elif any(summary.get(key, 0) for key in
             ("pending", "attention_required", "reconcile_pending", "reconcile_conflicts", "reconcile_skipped")):
        state = "attention_required"
    elif not summary.get("sessions") or summary.get("manifests_fetched") != summary.get("sessions"):
        state = "partial"
    else:
        state = "complete"
    summary["status"] = state
    now = int(time.time())
    if summary.get("sessions") and not scoped:
        self.storage.metadata_set("kobo_backup_last_attempt_at", now)
        # Fetching every manifest does not make the snapshot authoritative:
        # malformed, duplicated or interrupted source pages must not advance
        # the global manifest-success timestamp used by offline diagnostics.
        authoritative_sources = (
            len(summary.get("sources", ())) == summary["sessions"]
            and all(
                # Only terminal, fully processed source manifests are authoritative;
                # a fetch followed by cancellation/abort is not a complete snapshot.
                source.get("status") in {"complete", "attention_required"}
                and not source.get("malformed_manifest_items", 0)
                and not source.get("duplicate_manifest_items", 0)
                for source in summary["sources"]
            )
        )
        if (summary["manifests_fetched"] == summary["sessions"]
                and authoritative_sources):
            self.storage.metadata_set("kobo_backup_last_manifest_success_at", now)
        if state == "complete":
            self.storage.metadata_set("kobo_backup_last_success_at", now)
    if not scoped:
        self.storage.metadata_set("kobo_backup_last_batch_status", state)
    self.diagnostics.add(
        "KOBO", "backup sync",
        "ok" if state == "complete" else "error" if state in {"aborted", "failed"} else "warning",
        detail=_safe_kobo_backup_diagnostic_detail(summary),
    )
    emit("done", **{k: v for k, v in summary.items() if k not in {"error_examples", "error_messages"}})
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
            # Do not rewrite the acquired revision until reconciliation has
            # completed. A second sync must not relabel quarantined local
            # provenance with the newer remote revision.
            "revision_id": (
                current.get("revision_id")
                if current.get("status") in {
                    "ok", "complete", "reconcile_required", "reconcile_conflict",
                }
                and current.get("revision_id") != revision_id
                else revision_id
            ),
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



def _manifest_archived(raw, current):
    # A fresh explicit unarchive can supersede a prior 409; omission cannot.
    return raw.get("archived") is True or (
        raw.get("archived") is not False and current is not None
        and (current.get("status") == "skipped_archived"
             or current.get("attention_code") == "archived_book")
    )


def _skip_manifest_item(self, item, raw, session_target_dir, indexed_kobo_paths, adapter_available, adept_state, index, total, summary, emit, *, retry_scope_blocked: bool = False, session=None, declared_budget=None):
    revision_id, title, account_id, current, base_record = item
    product_id = base_record["item_id"]
    # A known archive is a terminal non-error, before reconciliation or any
    # provider acquisition. A previous 409 is not retried unless a new
    # manifest explicitly reports archived=False. Never downgrade a local
    # receipt or discard its path, SHA, rights or placement.
    archived = _manifest_archived(raw, current)
    if archived:
        if not current or current.get("status") not in {
                "ok", "complete", "reconcile_required", "reconcile_conflict"}:
            self.storage.backup_put({
                **(current or {}), **base_record, "status": "skipped_archived",
                "error": None, "attention_code": None,
            })
        summary["skipped_archived"] += 1
        emit("skipped_archived", current=index + 1, total=total,
             title=title, item_id=product_id, code="archived_book",
             message="Livre archivé sur Kobo : acquisition ignorée")
        return True

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
        if local.is_symlink():
            self.storage.backup_put({**current, "status": "reconcile_required", "error": "Lien symbolique local à réconcilier"})
            summary["reconcile_skipped"] += 1
            return True
        if local.is_file():
            try:
                observed = self._observed_backup_artifact(local)
            except ProviderConfigurationError:
                # A stale/invalid file must not retain a complete state.
                self.storage.backup_put({**current, "status": "reconcile_required", "error": "Artefact local invalide"})
                summary["reconcile_skipped"] += 1
                return True
            # The stored checksum describes the *acquired* revision, not
            # merely any structurally valid EPUB/PDF now occupying that path.
            # Never silently accept changed bytes or overwrite the baseline:
            # doing so would make a corrupted/replaced book look "unchanged".
            expected_hash = str(current.get("sha256") or "").strip().lower()
            expected_size = current.get("size")
            hash_changed = bool(expected_hash) and observed["sha256"] != expected_hash
            size_changed = (
                type(expected_size) is int and expected_size >= 0
                and observed["size"] != expected_size
            )
            if hash_changed or size_changed:
                self.storage.backup_put({
                    **current,
                    "status": "reconcile_required",
                    "error": "Artefact Kobo local modifié depuis la sauvegarde ; vérification manuelle requise",
                    "updated_at": int(time.time()),
                })
                summary["reconcile_pending"] += 1
                summary["reconcile_skipped"] += 1
                emit("reconcile_required", current=index + 1, total=total,
                     title=title, item_id=product_id)
                return True
            # Historic complete rows may lack a checksum: accept only after
            # format/DRM inspection, then persist the first verified baseline.
            self.storage.backup_put({**current, **observed})
            summary["unchanged"] += 1
            return True

    # Legacy/partial records can claim a completed Kobo download without
    # retaining its placement path. Missing provenance is not evidence of an
    # absent artifact: quarantine instead of acquiring another copy.
    if current and current.get("status") in {"ok", "complete"} and not str(
        current.get("local_path") or ""
    ).strip():
        self.storage.backup_put({
            **base_record,
            # Keep the acquired revision baseline, even if the remote edition
            # has changed since the previously completed acquisition.
            "revision_id": current.get("revision_id"),
            "status": "reconcile_required",
            "error": "Acquisition Kobo précédemment complète sans chemin local fiable ; rapprochement requis",
        })
        summary["reconcile_pending"] += 1
        summary["reconcile_skipped"] += 1
        emit("reconcile_required", current=index + 1, total=total,
             item_id=product_id, title=title)
        return True

    # A prior Kobo row may point to a nonstandard filename. Even when the
    # remote revision changes, never discard that placement or redownload
    # until a deliberate replacement/reconciliation contract exists.
    if current and current.get("local_path"):
        linked_path = Path(str(current["local_path"]))
        # A persisted path proves a prior placement was recorded, even if it
        # was moved or deleted after a crash. Never silently fetch a second copy.
        # Preserve dangling symlinks and absent files for manual reconciliation.
        linked_present = linked_path.is_symlink() or linked_path.is_file()
        self.storage.backup_put({
            **base_record,
            "status": "reconcile_required",
            "local_path": str(linked_path),
            "error": (
                "Artefact Kobo déjà lié : révision distante différente ou état incomplet ; rapprochement requis"
                if linked_present else
                "Artefact Kobo précédemment lié introuvable ; vérifiez son déplacement avant tout téléchargement"
            ),
        })
        summary["reconcile_pending"] += 1
        summary["reconcile_skipped"] += 1
        emit("reconcile_required", current=index + 1, total=total,
             title=title, item_id=product_id)
        return True

    # Unrelated imported books do not block delivery. However, never overwrite
    # or redownload a plausible, *unlinked* artifact for this exact item.
    # Filename matches, including damaged or symlinked files, are ambiguous:
    # quarantine for manual reconciliation rather than treating them as complete.
    # This includes legacy flat-library placements from older versions.
    candidates = self._kobo_record_candidate_paths(base_record, session_target_dir)
    identity_id = str(base_record.get("identity_id") or "")
    for indexed_path in indexed_kobo_paths.get((identity_id, product_id), ()):
        if indexed_path not in candidates:
            candidates.append(indexed_path)
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

    # A user-declared link is not a Kobo backup receipt. It may refer to a
    # moved or altered EPUB, so neither a matching declaration nor an unreadable
    # declaration store authorizes downloading a replacement. This check runs
    # under the existing acquisition lock for BOTH normal and confirmed sync,
    # after the existing verified-backup/filename guards and before provider I/O.
    # Revoked declarations no longer block: never persist reconcile_required
    # solely because of a statement whose active state may later change.
    if all(type(base_record.get(key)) is str and base_record[key]
           for key in ("identity_id", "credential_id", "account_id", "item_id")):
        from .library_declared_links import DeclarationRejected, has_active_declared_link
        try:
            declared = has_active_declared_link(
                self.storage, identity_id=base_record["identity_id"],
                provider_id="kobo", credential_id=base_record["credential_id"],
                account_id=base_record["account_id"], item_id=product_id,
            )
            declaration_error = False
        except DeclarationRejected:
            declared, declaration_error = False, True
        if declared or declaration_error:
            # Kobo sync already holds the global acquisition lock. Reuse the
            # exact scoped/fresh Core verifier without reacquiring that
            # non-reentrant lock, and keep one 8s budget per provider source.
            # A verified *personal declaration* counts as local presence,
            # never as a Kobo acquisition receipt.
            if (declared and session is not None and declared_budget is not None
                    and declared_budget["remaining"] > 0):
                from .library_declared_presence import verify_declared_presence
                storage_at_start = self.storage
                identity_id = base_record["identity_id"]
                credential_id = base_record["credential_id"]
                account_id = base_record["account_id"]

                def source_current():
                    return bool(
                        self.storage is storage_at_start
                        and session.connected
                        and session.provider_id == "kobo"
                        and session.credential_id == credential_id
                        and any(value is session for value in self._sessions.values())
                        and len(session.accounts) == 1
                        and session.accounts[0].identity_id == identity_id
                        and session.accounts[0].unique_id == account_id
                        and self.storage.identity_for_connection(
                            "kobo", credential_id) == identity_id
                    )

                started = time.monotonic()
                try:
                    presence = verify_declared_presence(
                        self, identity_id=identity_id, provider_id="kobo",
                        credential_id=credential_id, account_id=account_id,
                        item_id=product_id, source_current=source_current,
                        seconds=declared_budget["remaining"],
                        _acquisition_lock_held=True,
                    )
                finally:
                    declared_budget["remaining"] = max(
                        0.0, declared_budget["remaining"]
                        - max(0.0, time.monotonic() - started),
                    )
                if presence.status == "declared_current":
                    # No backup_put here: the compatibility projection would
                    # insert another entitlement for the same right. A personal
                    # declaration is NOT a backup row or provider receipt.
                    # Store only the scoped run checkpoint below.
                    summary["already_local_declared"] += 1
                    emit("already_local_declared", current=index + 1,
                         total=total, title=title, item_id=product_id)
                    return True
            code = ("declared_link_recorded" if declared
                    else "declared_links_unavailable")
            message = ("Correspondance locale déclarée : vérifier le fichier avant "
                       "toute nouvelle acquisition" if declared else
                       "Déclarations locales indisponibles : acquisition suspendue "
                       "jusqu'à vérification")
            self.storage.backup_put({
                **base_record, "status": "attention_required",
                "attention_code": code, "error": message,
            })
            summary["attention_required"] += 1
            emit("attention_required", current=index + 1, total=total,
                 title=title, item_id=product_id, code=code, message=message)
            return True

    if raw.get("downloadable") is False:
        summary["skipped"] += 1
        return True

    # Missing delegated scope persists across sessions and automatic sync attempts.
    # Only an explicit account-targeted retry after reauthorization bypasses it.
    if (not retry_scope_blocked and current
            and str(current.get("status") or "") == "attention_required"
            and str(current.get("attention_code") or "") == "missing_delegation_scope"):
        summary["attention_required"] += 1
        summary["items_blocked_by_scope"] += 1
        summary["blocked_non_retryable"] += 1
        emit(
            "attention_required", current=index + 1, total=total,
            item_id=product_id, title=title, code="missing_delegation_scope",
            message="Kobo-Vault : autorisation de délégation requise (scope kobo.acquisition.delegate)",
        )
        return True

    if not adapter_available:
        adept_error = adept_state.get("error") if isinstance(adept_state.get("error"), dict) else {}
        reason = str(adept_error.get("message") or "Kobo-Adept indisponible")
        self.storage.backup_put({**base_record, "status": "pending_acquisition", "error": reason})
        summary["pending"] += 1
        return True

    return False



def _acquire_manifest_item(self, session, session_identity, item, raw, session_target_dir, index, total, summary, emit, record_error, logger, *, item_guard=None):
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
        if item_guard is not None:
            item_guard(search_item, product_id, revision_id)
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
                # Reconciliation status is the evidence. Arbitrary runtime
                # exception text may include a signed URL or personal path.
                "error": "Rapprochement Kobo nécessaire",
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
        if str(info.get("code") or "") == "missing_delegation_scope":
            message = "Kobo-Vault : autorisation de délégation requise (scope kobo.acquisition.delegate)"
            self.storage.backup_put({
                **base_record,
                "status": "attention_required",
                "attention_code": "missing_delegation_scope",
                "error": message,
                "attempt_count": attempt_count,
                "last_attempt_at": attempted_at,
                "updated_at": int(time.time()),
            })
            summary["attention_required"] += 1
            summary["items_blocked_by_scope"] += 1
            emit("attention_required", current=index + 1, total=total,
                 item_id=product_id, title=title,
                 code="missing_delegation_scope", message=message)
            return False
        if str(info.get("code") or "") == "archived_book":
            self.storage.backup_put({
                **base_record, "status": "skipped_archived",
                "attention_code": None, "error": None,
                "attempt_count": attempt_count,
                "last_attempt_at": attempted_at,
                "executor": "kobo-adept", "updated_at": int(time.time()),
            })
            summary["skipped_archived"] += 1
            emit("skipped_archived", current=index + 1, total=total,
                 title=title, item_id=product_id, code="archived_book",
                 message=info.get("message"), status_code=info.get("status_code"))
            return False
        # Provider/Kobo runtime errors and even their code can be attacker
        # controlled. Keep typed phase + bounded HTTP status in TRACE, but
        # never a traceback, title, account, product ID, signed URL or body.
        raw_phase = info.get("phase")
        phase = raw_phase if raw_phase in {"vault", "adept", "integration"} else "other"
        raw_status = info.get("status_code")
        http_status = raw_status if type(raw_status) is int and 100 <= raw_status <= 599 else 0
        logger.warning(
            "TRACE kobo acquisition failed code=backup_item_failed phase=%s http_status=%s",
            phase, http_status,
        )
        self.storage.backup_put({
            **base_record, "status": "error", "error": "Sauvegarde Kobo impossible",
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

