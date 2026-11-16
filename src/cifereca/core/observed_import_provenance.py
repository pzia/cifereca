"""Source-neutral observed loan display linkage; never a provider receipt or right."""
from __future__ import annotations

from .observed_loan_scope import scope_key


def remember_observed_import(service, result, scope, *,
                             provider_id: str, provenance_field: str) -> None:
    """Persist an already verified import's exact display-only loan link."""
    if (type(provider_id) is not str or not provider_id
            or type(provenance_field) is not str or not provenance_field
            or scope_key(scope) is None):
        return
    from .manual_import_display_admission import verified_manual_import_candidate
    candidate = verified_manual_import_candidate(service, result)
    if candidate is None:
        return
    acquisition_id, path, artifact_id = candidate
    service.storage.acquisition_bind_observed_display_loan(
        acquisition_id, path=str(path), artifact_id=artifact_id, scope=scope,
        placement_provider_id=provider_id,
        provenance_field=provenance_field,
    )


def observed_import_links(storage, placements, local_keys, *,
                          provider_id: str, provenance_field: str):
    """Inert display projection; no entitlement or new materialization."""
    from .local_import_display_links import observed_import_display_links
    return observed_import_display_links(
        storage, placements, local_keys, provenance_field=provenance_field,
        placement_provider_id=provider_id, validate_scope=scope_key,
    )


def provider_scoped_observed_links(provider_id: str, links: object) -> dict:
    """Key a display hint by its exact provider, never by borrower alone.

    The legacy links cannot mint or verify rights. Malformed/unscoped hints
    are discarded rather than becoming cross-provider aliases.
    """
    if type(provider_id) is not str or not provider_id or type(links) is not dict:
        return {}
    return {
        (provider_id, *scope): local_key
        for scope, local_key in links.items()
        if type(scope) is tuple and len(scope) == 5
        and all(type(value) is str and bool(value) for value in scope)
        and type(local_key) is str and bool(local_key)
    }



def observed_loan_provenance_field(provider_id: object) -> str | None:
    """The frozen legacy manual-import *field grammar*, not provider authority.

    Every installed or future source matching the original writer grammar
    uses the prefix before its first hyphen. Dots/foreign objects are not
    silently normalized into a historical key.
    """
    from re import fullmatch

    if (type(provider_id) is not str
            or fullmatch(r"[a-z][a-z0-9-]{0,63}", provider_id) is None):
        return None
    return provider_id.split("-", 1)[0] + "_observed_loan"


def observed_import_links_for_sources(
    storage, placements, local_keys, *, providers,
) -> dict:
    """Read passive loan display links for exact stored entitlement providers.

    Provider name is merely a coordinate of the same local manual-import
    grammar used by the writer. A hint still requires a completed manual
    acquisition, unambiguous placement, exact provider, artifact and borrower.
    No entitlement, possession, file or runtime callback is created.
    """
    try:
        unique = set()
        for index, provider in enumerate(providers):
            if index >= 100_000:
                return {}
            if observed_loan_provenance_field(provider) is not None:
                unique.add(provider)
                if len(unique) > 64:
                    # A corrupt unbounded provider inventory must not
                    # cause an unbounded series of SQLite joins.
                    return {}
    except (TypeError, ValueError, RuntimeError):
        return {}

    result = {}
    for provider in sorted(unique):
        field = observed_loan_provenance_field(provider)
        scoped = provider_scoped_observed_links(
            provider, observed_import_links(
                storage, placements, local_keys,
                provider_id=provider, provenance_field=field,
            ),
        )
        # The identity/provider/credential/borrower/loan/edition composite
        # is disjoint across providers. Never select a first ambiguous alias.
        for key, value in scoped.items():
            if key in result and result[key] != value:
                result.pop(key, None)
            else:
                result[key] = value
    return result
