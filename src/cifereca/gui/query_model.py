"""Loaded search set: local filtering never calls provider services."""
from dataclasses import dataclass, asdict
from cifereca.core.search import filter_loaded_results, file_format
from cifereca.core.document_filter import (
    public_document_classification, filter_loaded_with_documentary_proofs,
)

FIELDS = ('all', 'title', 'author', 'isbn', 'subject', 'series', 'collection', 'language')

@dataclass(frozen=True)
class QueryState:
    text: str = ''
    field: str = 'all'
    scope: str = 'all'
    providers: tuple[str, ...] = ()
    types: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()

    def filter(self, rows):
        return filter_loaded_with_documentary_proofs(
            rows, self.text, field=self.field, scope=self.scope,
            providers=self.providers, content_types=self.types, languages=self.languages,
        )


def result_row(item, *, purchase_evidence=None, identity_id=None):
    """Render a verified purchase only with independent Core ledger evidence.

    Provider flags, inventory scenario and account coordinates are not rights.
    This pure projection never calls an upstream provider, SQLite or a plugin;
    absent or invalid evidence has the closed state remote_catalog.
    """
    from ..core.search_owned_evidence import search_result_has_verified_purchase
    verified_purchase = search_result_has_verified_purchase(
        item, purchase_evidence, current_identity_id=identity_id,
    )
    result = asdict(item)
    # asdict() includes every ClassificationEvidence.raw_code, which is
    # upstream-supplied and may contain sensitive data. Publish only the
    # versioned, fixed-vocabulary UI projection; never emit provenance raw.
    result['document_classification'] = public_document_classification(
        getattr(item, 'document_classification', None)
    )
    return {**result, 'key': item.id, 'item_id': item.record_id,
            'provider_id': item.source_provider, 'media_format': file_format(item.format),
            'state': 'remote_owned' if verified_purchase else 'remote_catalog'}


def online_search(service, state):
    results, warnings = [], []
    for provider in state.providers or ('all',):
        if provider == 'local': continue
        results.extend(service.search(state.text, state.scope, force=True,
                                      provider_id=provider, field=state.field, content_types=state.types))
        warnings.extend(getattr(service, 'last_search_warnings', ()) or ())
    return results, warnings



def progressive_online_search(service, state, on_batch, control=None):
    providers = tuple(provider for provider in state.providers if provider != 'local')
    return service.search_progressive(
        state.text, state.scope, force=True, provider_ids=providers, field=state.field,
        content_types=state.types, on_batch=on_batch, control=control,
    )

def merge_results(existing, incoming):
    merged = {f'{x.source_provider}:{x.id}': x for x in existing}
    for x in incoming:
        merged[f'{x.source_provider}:{x.id}'] = x
    return list(merged.values())


def apply_facet(state, field, value):
    from dataclasses import replace
    from cifereca.core.search import file_format, format_family
    from cifereca.language import normalize_language_tag
    from .facet_text import author_search_text
    if field == 'format':
        fmt = format_family(value)
        return replace(state, types=(fmt,)) if fmt else state
    if field == 'source':
        return replace(state, providers=(str(value),)) if value else state
    if field == 'language':
        language = normalize_language_tag(value)
        primary = language.split('-', 1)[0] if language else ''
        return replace(state, languages=(primary,)) if primary else state
    if field == 'support' and str(value) in {'physical','digital','unknown'}:
        return replace(state, scope=str(value))
    text = author_search_text(value) if field == 'author' else str(value)
    if field in {'series','collection','publisher'}:
        return replace(state,text=text,field='all') if text else state
    return replace(state, text=text, field=field) if text and field in FIELDS else state
