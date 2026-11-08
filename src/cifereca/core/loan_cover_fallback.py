"""Read-only local cover fallback for a *canonically proven* loan origin.

Do not join a loan to a cover by title, ISBN, generic provider/item or shared
Artifact hash alone. A source-scoped completed OriginLink, matching profile
Placement and verified embedded cover are required. No provider I/O or rights
decision is performed here.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping, Any
from urllib.parse import urlsplit

from ..models import Loan
from .loan_source_identity import loan_source_key


def _trusted_embedded_url(row: object, identity_id: str) -> tuple[str, str] | None:
    if type(row) is not dict or row.get("identity_id") != identity_id:
        return None
    artifact = row.get("artifact_id")
    url = row.get("thumbnail_url")
    meta = row.get("metadata")
    if (type(artifact) is not str or not artifact.startswith("sha256:")
            or type(url) is not str or type(meta) is not dict):
        return None
    resolution = meta.get("cover_resolution")
    if (type(resolution) is not dict
            or resolution.get("status") != "selected"
            or resolution.get("tier") != "embedded"
            or resolution.get("source") != "epub"
            or resolution.get("url") != url):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "file" or parsed.netloc or not parsed.path or parsed.query or parsed.fragment:
            return None
    except ValueError:
        return None
    return artifact, url


def proven_local_loan_cover_urls(
    loans: Iterable[Loan], *, identity_id: str,
    origins: Iterable[tuple[str, str, str, str, str, str, str]],
    local_rows: Iterable[Mapping[str, Any]],
) -> dict[str, str]:
    """Map exact source-qualified Loan keys to profile-local embedded covers.

    Origins come from the existing verified provider OriginLink query,
    which itself requires a present Placement and canonical Artifact, provider
    response, receipt and account. Local rows come from the existing
    identity-scoped recent-acquisition projection, which checks real local
    placement accessibility and content-hash validated cover cache files.

    One loan scope linking two different Artifacts is ambiguous even when
    both cached covers happen to be identical. Unknown borrower (None)
    is different from a valid empty primary-account ID ("").
    """
    if type(identity_id) is not str or not identity_id:
        return {}

    by_artifact: dict[str, set[str]] = defaultdict(set)
    for row in local_rows:
        trusted = _trusted_embedded_url(row, identity_id)
        if trusted is not None:
            artifact, url = trusted
            by_artifact[artifact].add(url)

    scope_artifacts: dict[tuple[str, str, str, str, str], set[str]] = defaultdict(set)
    for origin in origins:
        if type(origin) is not tuple or len(origin) != 7:
            continue
        artifact, owner, provider, credential, borrower, edition, kind = origin
        if (owner != identity_id or kind != "loan"
                or any(type(x) is not str or not x for x in (
                    artifact, owner, provider, credential, edition,
                ))
                or type(borrower) is not str):
            continue
        if artifact in by_artifact:
            scope_artifacts[(owner, provider, credential, borrower, edition)].add(artifact)

    resolved: dict[str, str] = {}
    for loan in loans:
        if (type(loan) is not Loan or loan.identity_id != identity_id
                or loan.thumbnail_url
                or any(type(x) is not str or not x for x in (
                    loan.source_provider, loan.source_credential,
                ))
                or type(loan.source_account_id) is not str):
            continue
        edition = loan.record_id or loan.id
        if type(edition) is not str or not edition:
            continue
        artifacts = scope_artifacts.get((
            identity_id, loan.source_provider, loan.source_credential,
            loan.source_account_id, edition,
        ), set())
        if len(artifacts) != 1:
            continue
        urls = by_artifact[next(iter(artifacts))]
        if len(urls) == 1:
            resolved[loan_source_key(loan)] = next(iter(urls))
    return resolved


__all__ = ["proven_local_loan_cover_urls"]
