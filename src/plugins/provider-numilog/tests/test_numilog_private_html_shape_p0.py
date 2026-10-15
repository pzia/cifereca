"""#1883: offline HTML diagnostics never release a private Numilog delivery URL."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_numilog_owned_js_delivery_p0 import (
    SIGNED, SECOND, _card, _object, _page,
)

SCRIPT = Path(__file__).resolve().parents[4] / 'scripts' / 'numilog_html_shape.py'


def _run(tmp_path, html: str, *, context='unknown'):
    fixture = tmp_path / 'private-book-page-with-confidential-name.html'
    fixture.write_text(html, encoding='utf-8')
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), '--context', context, str(fixture)],
        capture_output=True, text=True, timeout=8,
        check=False,
    )
    return completed, json.loads(completed.stdout)


def _assert_redacted(completed, report):
    output = completed.stdout + completed.stderr
    for secret in (
        'SYNTHETIC_NOT_A_CREDENTIAL', 'SECOND_NOT_A_CREDENTIAL',
        'orderid=', 'auth=', 'ProductId', 'IndexExemplaire',
        'private-book-page-with-confidential-name', 'readBook.ashx?lid=',
        'Achat fictif', '9001', '9002',
    ):
        assert secret not in output and secret not in repr(report)


def test_meslivres_local_script_reports_only_scoped_shape_counts(tmp_path):
    html = _page(
        _card(12, 9001, SIGNED), _card(34, 9002, SECOND),
        outside_script=f'<script>window.other={_object(7777, SIGNED)};</script>',
    )
    completed, report = _run(tmp_path, html, context='meslivres')
    assert completed.returncode == 0
    assert completed.stderr == ''
    assert report == {
        'schema': 'numilog-html-shape/1',
        'declared_context': 'meslivres',
        'library_table_count': 1,
        'script_elements': 3,
        'js_delivery_shapes': 3,
        'download_action_count': 2,
        'library_structure': 'parsed',
        'book_cards': 2,
        'js_shapes_in_cards': 2,
        'js_shapes_outside_cards': 1,
        'bound_links': 2,
    }
    _assert_redacted(completed, report)


@pytest.mark.parametrize('context', ('book-detail', 'confirmation', 'readbook', 'unknown'))
def test_html_without_library_table_has_no_implied_book_binding(tmp_path, context):
    html = '<html><body><script>window.delivery=' + _object(9001, SIGNED) + ';</script></body></html>'
    completed, report = _run(tmp_path, html, context=context)
    assert completed.returncode == 0
    assert report['declared_context'] == context
    assert report['js_delivery_shapes'] == 1
    assert report['library_table_count'] == 0
    assert report['library_structure'] == 'not_applicable'
    assert report['bound_links'] == 0
    _assert_redacted(completed, report)


def test_two_titles_in_one_table_row_report_invalid_without_html_leak(tmp_path):
    html = (
        '<table id="myTable"><tr>'
        '<span class="pTitle">First private book</span>'
        '<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=12">First</a>'
        '<span class="pTitle">Second private book</span>'
        '<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=34">Second</a>'
        f'<script>window.delivery={_object(9001, SIGNED)};</script>'
        '</tr></table>'
    )
    completed, report = _run(tmp_path, html, context='meslivres')
    assert completed.returncode == 0
    assert report['library_structure'] == 'invalid_or_ambiguous'
    assert report['bound_links'] == 0
    assert report['js_delivery_shapes'] == 1
    _assert_redacted(completed, report)


def test_oversize_or_missing_page_produces_only_fixed_safe_failure(tmp_path):
    too_large = tmp_path / 'no-private-filename-in-response.html'
    too_large.write_bytes(b'0' * (4 * 1024 * 1024 + 8))
    for target in (too_large, tmp_path / 'missing-secret-page.html'):
        done = subprocess.run(
            [sys.executable, str(SCRIPT), '--context', 'unknown', str(target)],
            capture_output=True, text=True, timeout=8, check=False,
        )
        assert done.returncode == 2 and not done.stderr
        assert json.loads(done.stdout) == {
            'schema': 'numilog-html-shape/1',
            'status': 'unreadable_or_invalid',
        }
        assert 'secret' not in done.stdout
        assert str(target) not in done.stdout
