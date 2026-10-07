from __future__ import annotations

from typing import Any, Mapping

from cifereca.catalogue_link import catalogue_url

from .models import DocumentLink


def _row_metadata(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get('metadata')
    return value if isinstance(value, Mapping) else {}


def _safe_document_url(value: object) -> str:
    if not isinstance(value, str):
        return ''
    return str(catalogue_url({'friendly_url': value}) or '')


def document_links(row: Mapping[str, Any]) -> tuple[DocumentLink, ...]:
    """Project explicitly public documentary links without acquisition URLs."""
    metadata = _row_metadata(row)
    links: list[DocumentLink] = []
    seen: set[str] = set()

    notice = _safe_document_url(row.get('friendly_url') or metadata.get('friendly_url'))
    if notice:
        links.append(DocumentLink('notice', 'Ouvrir la notice', notice, 'notice'))
        seen.add(notice)

    raw_links = metadata.get('document_links')
    if isinstance(raw_links, (list, tuple)):
        for index, raw in enumerate(raw_links):
            if not isinstance(raw, Mapping):
                continue
            url = _safe_document_url(raw.get('url'))
            if not url or url in seen:
                continue
            relation = str(raw.get('relation') or 'related').strip().casefold() or 'related'
            label = str(raw.get('label') or '').strip()
            if not label:
                if relation == 'gallica':
                    label = 'Ouvrir dans Gallica'
                elif relation == 'notice':
                    label = 'Ouvrir la notice'
                else:
                    continue
            raw_id = str(raw.get('id') or '').strip()
            link_id = raw_id or f'document-{index}'
            links.append(DocumentLink(link_id, label, url, relation))
            seen.add(url)
    return tuple(links)


__all__ = ['document_links']
