"""Kobo Vault backup coordination over existing artifact/runtime interfaces.

Manifest authority remains provider.kobo; acquisition execution remains the
negotiated kobo-adept contract. State is owned by the service facade."""
from __future__ import annotations

from .source_context import source_context_epoch
from .canonical_delivery_abi import has_complete_delivery_abi
from .legacy_checkpoint_reader import _kobo_checkpoint_key, _kobo_checkpoint_for
from .legacy_diagnostic_projection import (
    _safe_kobo_opening_decision,
    safe_kobo_backup_report,
    _KOBO_DIAGNOSTIC_METRICS,
    _KOBO_DIAGNOSTIC_STATUSES,
    _safe_kobo_backup_diagnostic_detail,
)

import time
import os
import re
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable
from ..acquisition import Acquisition
from ..kobo import KoboAdeptError
from ..kobo.vault import KoboVaultError
from ..library_files import safe_library_component, sha256_file
from ..artifact_inspection import inspect_artifact
from ..paths import acquisition_lock_file
from ..models import SearchResult
from ..providers import ProviderConfigurationError
from ..plugin_api import UserFacingError, ProviderError


def _observed_backup_artifact(path: Path) -> dict[str, Any]:
    inspection = inspect_artifact(path)
    if not inspection.valid or not inspection.media_format or inspection.protected is True:
        raise ProviderConfigurationError("Artefact de sauvegarde invalide ou encore protégé")
    return {"format": inspection.media_format.upper(), "size": path.stat().st_size, "sha256": sha256_file(path)}


def _kobo_legacy_unlocated_byte_evidence(current: dict[str, Any] | None) -> bool:
    """A v0.7 SHA/size claim with no locator requires explicit reconciliation.

    It is neither a positive B presence proof nor permission to replace bytes.
    In particular, an old failed/pending row may still carry evidence of a
    partially completed opaque provider/ADEPT operation.
    """
    if not isinstance(current, dict):
        return False
    digest, size, path = (
        current.get("sha256"), current.get("size"), current.get("local_path"),
    )
    return (
        not str(path or "").strip()
        and ((type(digest) is str and bool(digest.strip()))
             or (type(size) is int and size > 0))
    )


def _kobo_legacy_has_integrity_baseline(current: dict[str, Any] | None) -> bool:
    """Historical *claim* adequate for byte comparison, never proof of B.

    A filename and a current EPUB hash cannot retroactively provide the
    missing SHA/size of an opaque Kobo delivery. This predicate is shared
    by the private W2 admission preview and the legacy sync executor so
    that an explicit confirmation cannot bypass the sync quarantine.
    """
    if not isinstance(current, dict):
        return False
    digest, size = current.get("sha256"), current.get("size")
    return (
        type(digest) is str
        and re.fullmatch(r"[0-9a-fA-F]{64}", digest) is not None
        and type(size) is int and size > 0
    )


def _kobo_legacy_current_path_scoped(
    service: Any, path: Path, target_dir: Path, *, identity_id: str,
) -> bool:
    """Read-only physical fence for one historical complete Kobo claim.

    An ordinary Kobo file or flat 0.7 library file remains compatible.
    An explicitly adopted same-identity *present* Placement may also live
    elsewhere (cross-reader transfer/recovery). A recorded backup never
    justifies following a symlink or reading a sibling identity's directory.
    No rights, OriginLinks or new Placements are produced by this check.
    """
    if (type(identity_id) is not str or not identity_id
            or not path.is_absolute() or ".." in path.parts):
        return False
    try:
        candidate = Path(os.path.abspath(path))
        target = Path(os.path.abspath(target_dir))
        library_root = Path(os.path.abspath(service.library_dir()))
        # Reject symlinks on the leaf OR any ancestor. Do not require the
        # identity to appear in config.identities(): imported identities may
        # already exist in the authoritative Storage/service profile list.
        if candidate.resolve(strict=True) != candidate:
            return False
        backup_root = target.parent.parent
        for identity in service.identities:
            if identity.identity_id == identity_id:
                continue
            other = str(identity.directory_name or "").strip()
            if not other or other in {".", ".."} or "/" in other or "\\" in other:
                return False
            if (candidate.is_relative_to(library_root / other)
                    or candidate.is_relative_to(backup_root / other / "kobo")):
                return False
        if candidate.is_relative_to(target) or candidate.parent == library_root:
            return True
        # A successful explicit B receipt can attach a file in an Outbox or
        # other owned folder. Accept that nonstandard path only when a live
        # canonical Placement already belongs to this exact identity.
        with service.storage._lock, service.storage._connect() as db:
            bound = db.execute(
                "SELECT identity_id,state FROM placements WHERE path=?",
                (str(candidate),),
            ).fetchone()
        return bound == (identity_id, "present")
    except (OSError, ValueError, RuntimeError, TypeError):
        return False


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
    """Retired 0.7 placement decision; canonical Core owns file placement."""
    raise ProviderConfigurationError("kobo_legacy_destination_retired")


def _execute_kobo_acquisition(self, item: SearchResult, target_dir: Path) -> Acquisition:
    """Retired D3 direct Kobo executor: use the selected canonical delivery API."""
    raise ProviderConfigurationError("kobo_legacy_direct_get_retired_use_canonical_provider")


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



def _canonical_kobo_backup_offline_preview(
    self, credential_id, identity_id, session, *, verify_local_files: bool,
):
    """D1 offline modern preview: historical canonical B, NEVER 0.7 backup.

    A persisted SourceReference/OriginLink is historical, not live provider A;
    even a physically verified B cannot grant a new GET without refreshing
    supplier rights and consent. Report separately verified old B copies,
    with NO positive acquisition-decision counters or missing-right claims.
    """
    from ..models import Account
    from .acquisition_audit import inspect_exact_source

    accounts = [
        row for row in self.accounts if type(row) is Account
        and row.provider_id == "kobo"
        and row.credential_id == credential_id
        and row.identity_id == identity_id
    ]
    if len(accounts) != 1:
        raise ProviderConfigurationError("canonical_kobo_preview_scope_unavailable")
    account = accounts[0]
    core_epoch = source_context_epoch(self)
    provider_epoch = getattr(session, "_epoch", None)

    def current():
        try:
            return bool(
                source_context_epoch(self) == core_epoch
                and self._sessions.get(self._session_key(
                    "kobo", credential_id,
                )) is session
                and getattr(session, "_epoch", None) == provider_epoch
                and session.connected is True
                and self.storage.provider_scope_account_bound(
                    identity_id=identity_id, provider_id="kobo",
                    credential_id=credential_id, account_id=account.unique_id,
                ) is True
                and sum(row is account for row in self.accounts) == 1
                and len(session.accounts) == 1
                # Kobo Vault's provider Account historically carries
                # identity_id="default" even when the Core/SQLite owner is
                # named. SQLite's exact enabled connection+borrower above is
                # authoritative; accept only this known adapter default.
                and session.accounts[0].identity_id in {identity_id, "default"}
                and session.accounts[0].provider_id == "kobo"
                and session.accounts[0].credential_id == credential_id
                and session.accounts[0].unique_id == account.unique_id
            )
        except Exception:
            return False

    if not current():
        raise ProviderConfigurationError("canonical_kobo_preview_scope_changed")
    try:
        evidence = inspect_exact_source(
            self.storage, account,
            library_root=(self.library_dir() if verify_local_files else None),
            verify_files=verify_local_files,
        )
    except Exception:
        raise ProviderConfigurationError("canonical_kobo_preview_unavailable") from None
    if not current():
        raise ProviderConfigurationError("canonical_kobo_preview_scope_changed")
    return _canonical_kobo_offline_historical_report(
        self, credential_id, identity_id, evidence,
        verify_local_files=verify_local_files,
    )

def _canonical_kobo_offline_historical_report(
    self, credential_id, identity_id, evidence, *, verify_local_files: bool,
):
    """One canonical B-only diagnostic shape for live and offline sources."""
    if (type(evidence) is not dict
            or evidence.get("scope") != "exact_source"
            or evidence.get("network") != "not_used"
            or evidence.get("mutations") != "none"
            or type(evidence.get("truncated")) is not bool
            or type(evidence.get("rights")) is not list
            or len(evidence["rights"]) > 512):
        raise ProviderConfigurationError("canonical_kobo_preview_unavailable")
    rights = evidence["rights"]
    # The audit is a *historical* projection: no right observed now, no
    # legacy 0.7 manifest to promote, no claim that an edition is missing.
    local_verified = sum(
        type(row) is dict
        and row.get("kind") == "purchase"
        and row.get("classification") == "complete_local"
        and row.get("file_verification") == "verified_sha256"
        for row in rights
    )
    counts = {
        "already_local_linked": 0, "already_local_verified": 0,
        "needs_reconciliation": 0, "attention_required": 0,
        "skipped_archived": 0, "blocked_scope": 0, "blocked_unlinked": 0,
        "not_downloadable": 0, "candidate_unverified": 0,
        "potentially_missing": 0, "invalid_manifest_items": 0,
    }
    return {
        "scope": "account",
        "source_id": _kobo_checkpoint_key(identity_id, credential_id).rsplit(".", 1)[-1],
        "manifest_refreshed": False, "manifest_items": 0,
        "known_item_count": 0, "provider_rejected_manifest_items": 0,
        "checkpoint_status": "not_applicable",
        "unlinked_local_books": 0, "unlinked_local_books_observed": False,
        "counts": counts, "blocking_items": 0, "examples": [],
        "preview_only": True, "acquisition_authorized": False,
        "requires_manifest_validation": True, "manifest_valid": False,
        "canonical_source": True, "historical_canonical_only": True,
        "historical_canonical": {
            "recorded_rights": len(rights),
            "previously_published_files_physically_verified": local_verified,
            "file_verification_requested": verify_local_files,
            "inventory_truncated": evidence["truncated"],
        },
    }


def _canonical_kobo_disconnected_offline_preview(
    self, credential_id, identity_id, *, verify_local_files: bool,
):
    """Inspect persisted exact Kobo B without loading the provider or v0.7.

    An offline account is a read-only coordinate object, never a Core
    registration or a permission for rights A/GET. The storage join and
    physical evidence are rechecked across the bounded canonical audit.
    """
    from ..models import Account
    from .acquisition_audit import inspect_exact_source

    storage = self.storage
    epoch = source_context_epoch(self)
    try:
        records = storage.provider_accounts()
        borrowers = [
            row for row in records if type(row) is dict
            and row.get("provider_id") == "kobo"
            and row.get("credential_id") == credential_id
        ]
        if (len(borrowers) != 1
                or borrowers[0].get("identity_id") != identity_id
                or type(borrowers[0].get("account_id")) is not str
                or self.identity_by_id(identity_id) is None):
            raise ProviderConfigurationError("canonical_kobo_preview_scope_unavailable")
        borrower = borrowers[0]["account_id"]
        bound_id = borrowers[0].get("provider_account_id")
        account = Account(
            display_name="", unique_id=borrower,
            provider_id="kobo", credential_id=credential_id,
            provider_label="Kobo", identity_id=identity_id,
        )

        def current():
            if (self.storage is not storage
                    or source_context_epoch(self) != epoch
                    or storage.identity_for_connection("kobo", credential_id) != identity_id
                    or storage.provider_scope_account_bound(
                        identity_id=identity_id, provider_id="kobo",
                        credential_id=credential_id, account_id=borrower,
                    ) is not True
                    or self.identity_by_id(identity_id) is None):
                return False
            registered = [
                row for row in storage.provider_accounts()
                if type(row) is dict and row.get("provider_id") == "kobo"
                and row.get("credential_id") == credential_id
            ]
            return bool(
                len(registered) == 1
                and registered[0].get("provider_account_id") == bound_id
                and registered[0].get("identity_id") == identity_id
                and registered[0].get("account_id") == borrower
            )

        if not current():
            raise ProviderConfigurationError("canonical_kobo_preview_scope_unavailable")
        evidence = inspect_exact_source(
            storage, account,
            library_root=self.library_dir() if verify_local_files else None,
            verify_files=verify_local_files,
        )
        if not current():
            raise ProviderConfigurationError("canonical_kobo_preview_scope_changed")
    except ProviderConfigurationError:
        raise
    except Exception:
        raise ProviderConfigurationError("canonical_kobo_preview_unavailable") from None
    return _canonical_kobo_offline_historical_report(
        self, credential_id, identity_id, evidence,
        verify_local_files=verify_local_files,
    )


def _canonical_kobo_backup_preview(self, credential_id, identity_id, session):
    """D1 live preview from authenticated A + verified B, not backup_items.

    Only sessions with the complete Core delivery ABI take this route.
    The old offline diagnostic and incomplete-ABI private W2 preflight remain
    compatibility reads until their cutover, never authorizing typed GET.
    """
    from ..models import Account
    from .rights_snapshot_read import read_account_rights_snapshot
    from .convergence_verified_presence import plan_existing_verified_presence

    registered = [
        account for account in self.accounts if type(account) is Account
        and account.provider_id == "kobo"
        and account.credential_id == credential_id
        and account.identity_id == identity_id
    ]
    if len(registered) != 1:
        raise ProviderConfigurationError("canonical_kobo_preview_scope_unavailable")
    account = registered[0]
    core_epoch = source_context_epoch(self)
    provider_epoch = getattr(session, "_epoch", None)

    def scope_current() -> bool:
        try:
            return bool(
                source_context_epoch(self) == core_epoch
                and self._sessions.get(
                    self._session_key("kobo", credential_id),
                ) is session
                and getattr(session, "_epoch", None) == provider_epoch
                and session.connected is True
                and self.storage.identity_for_connection(
                    "kobo", credential_id,
                ) == identity_id
                and self.sync_source_enabled(account) is True
                and sum(row is account for row in self.accounts) == 1
                and len(session.accounts) == 1
                # The supplier's borrower is a four-coordinate source, not
                # merely the upstream account ID. A reconnect/rebind can
                # preserve that ID under another provider or credential.
                and session.accounts[0].provider_id == "kobo"
                and session.accounts[0].credential_id == credential_id
                and session.accounts[0].identity_id in {identity_id, "default"}
                and session.accounts[0].unique_id == account.unique_id
            )
        except Exception:
            return False

    if not scope_current():
        raise ProviderConfigurationError("canonical_kobo_preview_scope_changed")
    try:
        snapshot = read_account_rights_snapshot(self, account)
        if (snapshot.status not in {"success", "partial"}
                or snapshot.scope.identity_id != identity_id
                or snapshot.scope.provider_id != "kobo"
                or snapshot.scope.credential_id != credential_id
                or snapshot.scope.account_id != account.unique_id
                or (snapshot.rights and not snapshot.branch)
                or any(not right.provider_attested for right in snapshot.rights)):
            raise ProviderConfigurationError("canonical_kobo_preview_unavailable")
        plan = plan_existing_verified_presence(
            self.storage, snapshot, library_root=self.library_dir(),
        )
    except ProviderConfigurationError:
        raise
    except Exception:
        # A provider error may contain credentials, paths or signed URLs.
        raise ProviderConfigurationError("canonical_kobo_preview_unavailable") from None
    if not scope_current():
        raise ProviderConfigurationError("canonical_kobo_preview_scope_changed")

    counts = {
        "already_local_linked": 0, "already_local_verified": 0,
        "needs_reconciliation": 0, "attention_required": 0,
        "skipped_archived": 0, "blocked_scope": 0, "blocked_unlinked": 0,
        "not_downloadable": 0, "candidate_unverified": 0,
        "potentially_missing": 0, "invalid_manifest_items": 0,
    }
    reason_by_state = {
        "present_attested": "already_local_verified",
        "present_declared": "needs_reconciliation",
        "needs_reconciliation": "needs_reconciliation",
        "missing_to_download": "potentially_missing",
        "pending": "potentially_missing",
        "inactive": "not_downloadable",
        "skipped_archived": "skipped_archived",
    }
    examples = []
    for decision in plan.decisions:
        reason = reason_by_state.get(decision.state)
        if reason is None:
            raise ProviderConfigurationError("canonical_kobo_preview_unavailable")
        counts[reason] += 1
        if (len(examples) < 20 and reason not in {
                "already_local_verified", "already_local_linked",
        }):
            # The same opaque, non-reversible digest shape as the 0.7 UI.
            digest = hashlib.sha256(
                (credential_id + "\x00" + decision.source.edition_id).encode("utf-8"),
            ).hexdigest()[:20]
            examples.append({"item_ref": digest, "reason": reason})
    return {
        "scope": "account",
        "source_id": _kobo_checkpoint_key(identity_id, credential_id).rsplit(".", 1)[-1],
        "manifest_refreshed": True,
        "manifest_items": len(snapshot.rights),
        "known_item_count": len(snapshot.rights),
        "provider_rejected_manifest_items": 0,
        "checkpoint_status": "not_applicable",
        "unlinked_local_books": 0,
        "unlinked_local_books_observed": False,
        "counts": counts,
        "blocking_items": sum(counts[name] for name in (
            "needs_reconciliation", "attention_required", "blocked_scope",
            "blocked_unlinked", "not_downloadable", "invalid_manifest_items",
        )),
        "examples": examples,
        "preview_only": True,
        "acquisition_authorized": False,
        "requires_manifest_validation": False,
        "manifest_valid": True,
        "canonical_source": True,
    }


def kobo_backup_preview(
    self, credential_id: str, *,
    refresh_manifest: bool = False, verify_local_files: bool = False,
    offline_canonical_only: bool = False,
    _manifest_evidence: tuple[list, int] | None = None,
    _item_reasons: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Read-only Kobo D1: provider A (complete ABI) or attested historical B.

    Neither a legacy manifest, an old 0.7 checkpoint, nor local file bytes
    constitute current source rights. A partial ABI cannot refresh A, provide
    an acquisition plan, or authorize a legacy fallback.
    """
    credential_id = str(credential_id or "").strip()
    identity_id = self.storage.identity_for_connection("kobo", credential_id)
    if not credential_id or not identity_id:
        raise ProviderConfigurationError("Compte Kobo demandé non configuré")
    if _manifest_evidence is not None or _item_reasons is not None:
        # D1/D2 private legacy evidence has no Core delivery authority.
        raise ProviderConfigurationError("canonical_kobo_preview_abi_unavailable")
    if offline_canonical_only:
        if refresh_manifest:
            raise ProviderConfigurationError("canonical_kobo_preview_scope_unavailable")
        return _canonical_kobo_disconnected_offline_preview(
            self, credential_id, identity_id,
            verify_local_files=verify_local_files,
        )
    session = next((
        value for value in self._sessions.values()
        if value.provider_id == "kobo" and value.credential_id == credential_id
        and value.connected and getattr(value.capabilities, "backup", False)
    ), None)
    if session is None:
        if refresh_manifest:
            raise ProviderConfigurationError("canonical_kobo_preview_abi_unavailable")
        return _canonical_kobo_disconnected_offline_preview(
            self, credential_id, identity_id,
            verify_local_files=verify_local_files,
        )
    if not has_complete_delivery_abi(session):
        if refresh_manifest:
            raise ProviderConfigurationError("canonical_kobo_preview_abi_unavailable")
        return _canonical_kobo_backup_offline_preview(
            self, credential_id, identity_id, session,
            verify_local_files=verify_local_files,
        )
    if refresh_manifest:
        return _canonical_kobo_backup_preview(
            self, credential_id, identity_id, session,
        )
    return _canonical_kobo_backup_offline_preview(
        self, credential_id, identity_id, session,
        verify_local_files=verify_local_files,
    )


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


def _kobo_backup_due(
    self, *, credential_id: str | None = None,
    interval_minutes: int | None = None,
) -> bool:
    # Manual/legacy callers retain their historical configuration. The
    # explicit on_start scheduler passes its pinned exact-source interval,
    # so a late change to the retired global value cannot change that scope.
    if interval_minutes is None:
        try:
            interval_minutes = max(
                1, int(self.config.get("kobo_backup.interval_minutes", 60) or 60)
            )
        except (TypeError, ValueError):
            interval_minutes = 60
    elif type(interval_minutes) is not int or not 60 <= interval_minutes <= 30 * 24 * 60:
        return False  # Invalid explicit source cadence must fail closed.
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
    """Retired legacy W2 facade: never invoke a fallback or acquire its lock."""
    raise ProviderConfigurationError("kobo_legacy_w2_retired_use_canonical_provider")


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


def _verified_canonical_kobo_unlinked_exemptions(
    service: Any, identity_id: str, candidate_paths: set[str],
) -> set[str]:
    """Exclude independently published canonical Kobo B from 'unlinked'.

    An old backup_items path was historically the only exclusion. Once an
    exact legacy row is retired after canonical delivery, the same file must
    not reappear as a foreign/orphan library book and block another source.
    Neither title, filename, SHA alone nor an arbitrary catalogue provider
    label is sufficient: require current active SourceReference, provider
    OriginLink, unique registered profile, enabled exact account, present
    Placement and freshly verified current bytes. This only refines a
    diagnostic count. It cannot authorize a GET, right or file adoption.
    """
    if (type(identity_id) is not str or not identity_id
            or not candidate_paths or len(candidate_paths) > 256):
        return set()
    from .convergence_verified_presence import _verified_copy
    import sqlite3

    try:
        root = Path(service.library_dir())
        with service.storage._lock, service.storage._connect() as db:
            db.execute("BEGIN")
            owner = db.execute(
                "SELECT directory_name FROM identities WHERE identity_id=?",
                (identity_id,),
            ).fetchone()
            if (owner is None or type(owner[0]) is not str
                    or not owner[0] or owner[0] in {".", ".."}
                    or "/" in owner[0] or "\\" in owner[0]
                    or db.execute(
                        "SELECT 1 FROM identities WHERE directory_name=? "
                        "AND identity_id<>? LIMIT 1",
                        (owner[0], identity_id),
                    ).fetchone() is not None):
                return set()
            # DISTINCT avoids one Artifact/Placement multiplying through
            # multiple provider rights. The cap fails closed instead of
            # claiming an exhaustive local provenance scan.
            rows = db.execute(
                "SELECT DISTINCT p.path,a.sha256,a.size FROM placements p "
                "JOIN artifacts a ON a.artifact_id=p.artifact_id "
                "JOIN origin_links o ON o.artifact_id=p.artifact_id "
                "AND o.identity_id=p.identity_id AND o.origin_kind='provider' "
                "JOIN source_references s ON s.source_reference_id=o.source_reference_id "
                "AND s.identity_id=p.identity_id "
                "WHERE p.identity_id=? AND p.state='present' "
                "AND s.provider_id='kobo' AND s.state='active' "
                "AND s.evidence_kind='provider_response' AND s.evidence_ref<>'' "
                "AND o.evidence_ref<>'' "
                "AND EXISTS (SELECT 1 FROM provider_connections c "
                "JOIN provider_accounts pa ON pa.connection_id=c.connection_id "
                "WHERE c.provider_id='kobo' AND c.credential_id=s.credential_id "
                "AND c.identity_id=s.identity_id AND c.enabled=1 "
                "AND pa.provider_id='kobo' AND pa.credential_id=s.credential_id "
                "AND pa.identity_id=s.identity_id AND pa.account_id=s.account_id) "
                "LIMIT 257",
                (identity_id,),
            ).fetchall()
            if len(rows) > 256:
                return set()
            admitted: set[str] = set()
            for path, sha, size in rows:
                if type(path) is not str:
                    continue
                normalized = str(Path(path).resolve(strict=False))
                if normalized not in candidate_paths:
                    continue
                if _verified_copy(
                    path, root=root, owner_directory=owner[0],
                    registered_directories=frozenset((owner[0],)),
                    size=size, sha256=sha,
                ):
                    admitted.add(normalized)
            return admitted
    except (sqlite3.Error, OSError, TypeError, ValueError, RuntimeError):
        return set()


def kobo_unlinked_local_count(self, identity_id: str) -> int:
    """Count actual local books without independently attested provenance.

    A historical 0.7 backup_items.local_path is only an archive/recovery
    locator, including when status='complete'. It cannot certify a file or
    suppress this count. A separately attested received copy or exact Core B
    SourceReference+OriginLink+currently hashed Placement remains exempt.
    _indexed_kobo_catalog_paths still gates per-edition reconciliation.
    """
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
            if path.is_file():
                if _attested_received_kobo_copy(
                    row, path, identity_id=identity_id, placements=placements,
                ):
                    continue
                paths.add(str(path.resolve()))
    # A fully independent Core B publication may outlive the 0.7 row.
    # Verify its current SourceReference/OriginLink + physical bytes instead
    # of resurrecting an orphan count (or trusting the old archive).
    return len(
        paths - _verified_canonical_kobo_unlinked_exemptions(self, identity_id, paths)
    )


def _sync_kobo_backup_locked(self, progress: Callable[[dict[str, Any]], None] | None=None, *, logger, credential_id: str | None = None, cancelled: Callable[[], bool] | None = None, retry_scope_blocked: bool = False, confirmed_manifest: list | None = None, source_guard: Callable[[], None] | None = None, item_guard: Callable[[SearchResult, str, str | None], None] | None = None, on_delivery_committed: Callable[[], None] | None = None) -> dict[str, Any]:
    """Retired W2 compatibility boundary. No historical execution authority."""
    raise ProviderConfigurationError("kobo_legacy_w2_retired_use_canonical_provider")

