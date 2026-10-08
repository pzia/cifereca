from __future__ import annotations

from typing import Any, Mapping

from cifereca.catalogue_link import catalogue_notice_url

from .models import DocumentLink


def _row_metadata(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get('metadata')
    return value if isinstance(value, Mapping) else {}


def _safe_document_url(value: object, *, provider: str = '') -> str:
    if not isinstance(value, str):
        return ''
    return str(catalogue_notice_url({'friendly_url': value}, provider=provider) or '')


def document_links(row: Mapping[str, Any]) -> tuple[DocumentLink, ...]:
    """Project explicitly public documentary links without acquisition URLs."""
    metadata = _row_metadata(row)
    provider = str(row.get('provider_id') or row.get('source_provider') or '')
    links: list[DocumentLink] = []
    seen: set[str] = set()

    notice = catalogue_notice_url(row, provider=provider)
    if notice:
        links.append(DocumentLink('notice', 'Ouvrir la notice', notice, 'notice'))
        seen.add(notice)

    raw_links = metadata.get('document_links')
    if isinstance(raw_links, (list, tuple)):
        for index, raw in enumerate(raw_links):
            if not isinstance(raw, Mapping):
                continue
            url = _safe_document_url(raw.get('url'), provider=provider)
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
