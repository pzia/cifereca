"""#4030: private CoreUI admission for a known-C documentary preview.

Capture on explicit operator action, execute on worker; no public plugin
capability, filesystem mutation, provider request or origin writer.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path

from .library_audit_roots import configured_identity_roots
from .local_origin_suggestion_preview import (
    _SuggestionContinuation, suggest_local_placement_documents,
)
from .local_origin_documentary_suggestions import DocumentarySuggestionPage
from .source_context import source_context_epoch


def _root(config):
    raw = config.get("library.directory", "~/EBooks")
    if type(raw) is not str or not raw.strip() or chr(0) in raw:
        raise ValueError("invalid_library_root")
    root = Path(os.path.expandvars(os.path.expanduser(raw.strip())))
    if not root.is_absolute():
        root = Path.home() / root
    if root == Path("/") or ".." in root.parts:
        raise ValueError("invalid_library_root")
    return root


def _navigation_epoch(service):
    epoch = getattr(service, "_account_context_epoch", None)
    if type(epoch) is not int or epoch < 0:
        raise ValueError("invalid_navigation_epoch")
    return epoch


def _binding_current(storage, identity, directory):
    # The binding helper owns a connection context. Never allow a preview
    # guard to settle an unrelated write already on this worker connection.
    # The connection is thread-local; the binding API owns the lock.
    if storage._connect().in_transaction:
        return False
    return storage.exact_profile_directory_binding(identity) == (directory, True)


@dataclass(frozen=True, slots=True, repr=False)
class LocalOriginSuggestionRequest:
    service: object
    owner: object
    identity_id: str
    directory: str
    placement_id: str
    storage: object
    database: Path
    config: object
    roots: tuple[Path, ...]
    root: Path
    source_epoch: int
    navigation_epoch: int
    page: int
    page_size: int
    continuation: _SuggestionContinuation | None = None

    def current(self):
        try:
            s = self.service
            return (
                s.current_identity is self.owner
                and s.identity_by_id(self.identity_id) is self.owner
                and self.owner.identity_id == self.identity_id
                and self.owner.directory_name == self.directory
                and s.storage is self.storage
                and Path(s.storage.path) == self.database
                and s.config is self.config
                and _root(s.config) == self.root
                and configured_identity_roots(s.config, self.identity_id)[self.identity_id] == self.roots
                and _binding_current(s.storage, self.identity_id, self.directory)
                and source_context_epoch(s) == self.source_epoch
                and _navigation_epoch(s) == self.navigation_epoch
            )
        except Exception:
            return False


def admit_local_origin_suggestion(service, *, identity_id: str, placement_id: str,
                                 confirmed: bool = False, page=0, page_size=20):
    """Capture a private exact scope before a worker is enqueued; no mkdir."""
    if (confirmed is not True
            or any(type(item) is not str or not 0 < len(item) <= 256
                   or any(ord(c) < 32 or ord(c) == 127 for c in item)
                   for item in (identity_id, placement_id))
            or type(page) is not int or page != 0
            or type(page_size) is not int or not 1 <= page_size <= 50):
        return None
    try:
        epoch = source_context_epoch(service)
        nav = _navigation_epoch(service)
        owner = service.current_identity
        if owner is None or owner.identity_id != identity_id:
            return None
        root = _root(service.config)
        directory = owner.directory_name
        roots = configured_identity_roots(service.config, identity_id)[identity_id]
        if (type(directory) is not str or not directory
                or root / directory not in roots):
            return None
        request = LocalOriginSuggestionRequest(
            service, owner, identity_id, directory, placement_id, service.storage,
            Path(service.storage.path), service.config, roots, root, epoch, nav, page, page_size,
        )
        return request if request.current() else None
    except Exception:
        return None


def continue_local_origin_suggestion(service, request, result):
    """Advance only a valid prior page from this private operator scope."""
    if (type(request) is not LocalOriginSuggestionRequest
            or request.service is not service
            or not request.current()
            or type(result) is not dict
            or result.get("status") != "partial"):
        return None
    page = result.get("suggestions")
    next_page = result.get("continuation")
    if (type(page) is not DocumentarySuggestionPage or page.has_more is not True
            or page.page != request.page or page.page_size != request.page_size
            or type(next_page) is not _SuggestionContinuation
            or next_page.storage is not request.storage or next_page.root != request.root
            or next_page.identity != request.identity_id
            or next_page.placement != request.placement_id
            or next_page.next_page != request.page + 1 or next_page.page_size != request.page_size
            or next_page.next_page > 200):
        return None
    return replace(request, page=next_page.next_page, continuation=next_page)


def run_local_origin_suggestion(service, request):
    """Revoked request exposes no page; check again before handing to UI."""
    def excluded():
        return {
            "status": "excluded", "reason": "scope_changed",
            "suggestions": None, "continuation": None, "metadata_evidence": "not_checked",
            "association_authorized": False, "may_write_origin_or_right": False,
            "provider_access_performed": False,
        }

    if (type(request) is not LocalOriginSuggestionRequest
            or request.service is not service or not request.current()):
        return excluded()
    try:
        page = suggest_local_placement_documents(
            request.storage, library_root=request.root,
            identity_id=request.identity_id, placement_id=request.placement_id,
            explicitly_requested=True, page=request.page, page_size=request.page_size,
            continuation=request.continuation,
        )
        return page if request.current() else excluded()
    except Exception:
        return excluded()
