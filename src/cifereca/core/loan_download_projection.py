"""Read-only provider loan payloads; acquisition orchestration stays in services.

No provider, cache, filesystem, hook, ADEPT, or ledger operation belongs here.
Constructors are injected at the call boundary to preserve the existing
cifereca.services.SearchResult and Acquisition patchpoints.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..models import Loan, SearchResult
from ..acquisition import Acquisition


def build_hook_item(
    loan: Loan, url: str, *, result_factory: Callable[..., SearchResult],
) -> SearchResult:
    return result_factory(
        id=loan.id, record_id=str(loan.record_id or loan.id), docbase=loan.provider,
        medium="digital", title=loan.title, author=None, type=loan.document_type,
        format="ACSM", publisher=None, published_at=None, description=None,
        thumbnail_url=loan.thumbnail_url, friendly_url=loan.ebook_url, scenario="LOAN",
        source_provider=loan.source_provider, source_credential=loan.source_credential,
        download_url=url, content_type="ebook",
    )


def build_acquisition(
    loan: Loan, *, account_key: str, path: Path, url: str,
    is_acsm: bool, mime_type: str | None, library_target_dir: Path,
    acquisition_factory: Callable[..., Acquisition],
) -> Acquisition:
    """Describe the final provider stage, without classifying or moving a file."""
    return acquisition_factory(
        provider_id=loan.source_provider,
        credential_id=loan.source_credential,
        account_id=account_key,
        item_id=str(loan.record_id or loan.id),
        loan_id=loan.id,
        kind="acsm" if is_acsm else "already_local",
        source_url=url or None,
        local_path=path,
        mime_type=mime_type,
        drm="adept" if is_acsm else "unknown",
        expires_at=loan.due_at,
        metadata={
            "title": loan.title,
            "cover_url": loan.thumbnail_url,
            "content_type": "ebook",
            "format": path.suffix.lstrip(".").upper() or None,
        },
        acsm_path=path if is_acsm else None,
        artifact_path=None if is_acsm else path,
        target_dir=library_target_dir,
        action="download",
    )
