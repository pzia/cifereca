#!/usr/bin/env python3
"""Offline, privacy-safe Numilog HTML *shape* triage for P0 #1883.

Usage:
    python scripts/numilog_html_shape.py --context book-detail /private/saved.html

Never reads browser cookies, never sends HTTP, never writes a report file and
never emits HTML, signed URLs, titles, ProductId, lid, filenames or token values.
Treat the input file itself as secret: do not commit or upload it.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

MAX_BYTES = 4 * 1024 * 1024
CONTEXTS = ('unknown', 'meslivres', 'book-detail', 'readbook', 'confirmation')


def analyze(raw: bytes, *, context: str = 'unknown') -> dict[str, object]:
    """Return a strict safe vocabulary of booleans, bounded counts and enums."""
    if context not in CONTEXTS:
        raise ValueError('invalid_context')
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise ValueError('invalid_size')
    plugin = Path(__file__).resolve().parents[1] / 'src/plugins/provider-numilog'
    if str(plugin) not in sys.path:
        sys.path.insert(0, str(plugin))
    from cifereca_numilog_html import (
        Parser, ParseError, _js_delivery_shape_count,
        parse_library_page,
    )
    try:
        doc = Parser(raw.decode('utf-8-sig', errors='replace')).root
        tables = [n for n in doc.walk() if n.attrs.get('id') == 'myTable']
        scripts = [n for n in doc.walk() if n.tag == 'script']
        download_actions = [
            n for n in doc.walk()
            if n.tag == 'a' and 'LnkDownloadBook' in
            set((n.attrs.get('class') or '').split())
        ]
        result: dict[str, object] = {
            'schema': 'numilog-html-shape/1',
            'declared_context': context,  # user-selected, not inferred from URL
            'library_table_count': min(10000, len(tables)),
            'script_elements': min(10000, len(scripts)),
            'js_delivery_shapes': min(
                10000, sum(_js_delivery_shape_count(s) for s in scripts)
            ),
            'download_action_count': min(10000, len(download_actions)),
            'library_structure': 'not_applicable',
            'book_cards': 0,
            'js_shapes_in_cards': 0,
            'js_shapes_outside_cards': 0,
            'bound_links': 0,
        }
        if len(tables) == 1:
            structure: dict[str, int] = {}
            try:
                parse_library_page(
                    raw.decode('utf-8-sig', errors='replace'),
                    delivery_evidence=True,
                    delivery_diagnostics=structure,
                )
            except ParseError:
                result['library_structure'] = 'invalid_or_ambiguous'
            else:
                result['library_structure'] = 'parsed'
                result['book_cards'] = structure.get('cards', 0)
                result['js_shapes_in_cards'] = structure.get('js_shapes_in_cards', 0)
                result['js_shapes_outside_cards'] = structure.get('js_shapes_outside_cards', 0)
                result['bound_links'] = structure.get('bound_links', 0)
        return result
    except Exception:
        # Never accidentally leak a private URL via exception or traceback.
        raise ValueError('invalid_html_structure') from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Numilog: rapport local expurgé de structure HTML (aucun réseau).',
    )
    parser.add_argument('--context', choices=CONTEXTS, default='unknown')
    parser.add_argument('html_file', type=Path)
    args = parser.parse_args(argv)
    try:
        # Bound input size even for unexpected binary files.
        with args.html_file.open('rb') as file:
            raw = file.read(MAX_BYTES + 1)
        report = analyze(raw, context=args.context)
    except (OSError, ValueError):
        print(json.dumps({'schema': 'numilog-html-shape/1', 'status': 'unreadable_or_invalid'}))
        return 2
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
