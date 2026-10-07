from __future__ import annotations

from collections.abc import Mapping

from cifereca.providers import ProviderSearchCapabilities


_FIELD_LABELS = {
    'all': 'Tous',
    'title': 'Titre',
    'author': 'Auteur',
    'isbn': 'ISBN',
    'subject': 'Sujet',
    'series': 'Série',
    'collection': 'Collection',
    'language': 'Langue',
}


def provider_search_summary(
    contracts: Mapping[str, ProviderSearchCapabilities],
    provider_ids: tuple[str, ...] = (),
) -> str:
    """Human-readable search limits for CoreUI; no capability is inferred."""
    selected_ids = tuple(str(value) for value in provider_ids if str(value) and str(value) != 'local')
    if selected_ids:
        selected = [(pid, contracts[pid]) for pid in selected_ids if pid in contracts]
    else:
        selected = list(contracts.items())
    declared = [(pid, contract) for pid, contract in selected if contract.declared]
    if not declared:
        return ''

    field_sets = [set(contract.fields) for _pid, contract in declared if contract.fields]
    common_fields = set.intersection(*field_sets) if field_sets else set()
    parts: list[str] = []
    if len(declared) == 1:
        contract = declared[0][1]
        if contract.fields:
            labels = [_FIELD_LABELS.get(field, field) for field in contract.fields]
            parts.append('Champs : ' + ', '.join(labels))
        if contract.pagination == 'none':
            parts.append('Pagination non prise en charge')
        elif contract.pagination != 'unspecified':
            paging = 'Pagination : ' + contract.pagination
            if contract.page_size:
                paging += f' · {contract.page_size}/page'
            if contract.max_pages:
                paging += f' · max {contract.max_pages} pages'
            parts.append(paging)
    else:
        if common_fields:
            order = ('all','title','author','isbn','subject','series','collection','language')
            labels = [_FIELD_LABELS.get(field, field) for field in order if field in common_fields]
            parts.append('Champs communs : ' + ', '.join(labels))
        paging_modes = {contract.pagination for _pid, contract in declared}
        if len(paging_modes) == 1:
            mode = next(iter(paging_modes))
            if mode == 'none':
                parts.append('Pagination non prise en charge')
            elif mode != 'unspecified':
                parts.append('Pagination : ' + mode)
        else:
            parts.append('Pagination variable selon la source')

    if not parts:
        return 'Limites de recherche déclarées par la source'
    return ' · '.join(parts)


__all__ = ['provider_search_summary']
