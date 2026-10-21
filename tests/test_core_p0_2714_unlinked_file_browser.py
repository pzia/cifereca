"""P0 #2714: the local file browser filters and selects, never attaches.

No provider calls, online metadata, entitlement mutation or repair. Every
bibliographic identifier, filename and SHA remains private to this session.
"""
from __future__ import annotations

import json
import os

import pytest

from cifereca.core.library_audit import audit_library
from cifereca.core.library_inventory import InventoryLimits
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_core_p0_2657_inspection_cases import six_numilog
from tests.test_library_reconciliation import epub, filesystem


def _session(roots, database, *, limits=None):
    before = filesystem(database.parent)
    audit = audit_library(database, roots, limits=limits)
    session = LocalInspection(audit, lambda: True, next(iter(roots)))
    assert filesystem(database.parent) == before
    return audit, session


def test_six_numilog_unlinked_files_are_selectable_without_metadata_right_match(
    six_numilog,
):
    root, database = six_numilog
    audit, session = _session({"alice": (root,)}, database)
    page = session.unconfirmed_files(offset=0, limit=25, sort='title')
    assert page.total == 3
    assert len(page.cases) == 3
    assert all(case.key.startswith('file:') for case in page.cases)
    assert all('confirmed_link' not in case.states for case in page.cases)
    assert 'Titre caché 5' in page.cases[-1].label
    assert session.unconfirmed_files(query='titre cache 4').total == 1
    assert session.unconfirmed_files(query='code-5').total == 1
    assert session.unconfirmed_files(query='PRIVATE_NOT_IN_LIST').total == 0
    assert session.unconfirmed_files(
        media_format='epub', sha_state='verified', epub_crc='confirmed',
    ).total == 3
    assert session.unconfirmed_files(media_format='pdf').total == 0
    assert session.unconfirmed_files(sha_state='unverified').total == 0
    assert session.unconfirmed_files(epub_crc='other').total == 0
    assert session.page(state='rights_without_verified_file').total == 3
    assert session.candidate_files(session.page(
        state='rights_without_verified_file',
    ).cases[-1].key) == ()
    assert audit.redacted()['reconciliation']['counts']['confirmed_link'] == 3
    assert 'Titre caché' not in json.dumps(audit.redacted())
    assert 'PRIVATE' not in json.dumps(session.summary())
    session.close()
    assert not session._file_facets
    assert not session.unconfirmed_files().cases


def test_sha_unverified_still_visible_without_claiming_missing_file(six_numilog):
    root, database = six_numilog
    audit, session = _session({"alice": (root,)}, database,
                              limits=InventoryLimits(hash_bytes=1))
    page = session.unconfirmed_files(sha_state='unverified', limit=25)
    assert page.total == 6
    assert 'hash_byte_budget' in audit.inventory.reasons
    assert session.unconfirmed_files(epub_crc='confirmed').total == 0
    assert session.unconfirmed_files(sha_state='verified').total == 0
    assert session.summary()['status'] == 'incomplete_scan'
    assert all(case.key.startswith('file:') for case in page.cases)
    assert not any('confirmed_link' in case.states for case in page.cases)
    session.close()


def test_pdf_and_epub_filters_do_not_call_a_provider(six_numilog):
    root, database = six_numilog
    pdf = root / 'PRIVATE-LOCAL-PDF.pdf'
    pdf.write_bytes(b'%PDF-1.4\n' + b'synthetic document' * 4)
    _audit, session = _session({"alice": (root,)}, database)
    assert session.unconfirmed_files(media_format='pdf').total == 1
    pdf_page = session.unconfirmed_files(
        media_format='pdf', sha_state='verified', limit=20,
    )
    assert len(pdf_page.cases) == 1
    assert pdf_page.cases[0].key.startswith('file:')
    assert 'PDF' in pdf_page.cases[0].details.upper()
    assert session.unconfirmed_files(epub_crc='confirmed').total == 3
    assert session.unconfirmed_files(
        media_format='pdf', epub_crc='confirmed',
    ).total == 0
    session.close()


def test_many_unlinked_files_paginate_deterministically_over_100_books(
    six_numilog,
):
    root, database = six_numilog
    template = (root / 'code-5.epub').read_bytes()
    for number in range(120):
        (root / f'local-{number:03d}.epub').write_bytes(template)
    audit, session = _session({"alice": (root,)}, database)
    result = session.unconfirmed_files(limit=25, sort='filename')
    assert result.total == 123
    assert len(result.cases) == 25
    final = session.unconfirmed_files(offset=100, limit=25, sort='filename')
    assert final.total == 123
    assert len(final.cases) == 23
    unique = {case.key for case in result.cases + final.cases}
    assert len(unique) == 48
    filtered = session.unconfirmed_files(
        query='LOCAL-119', sort='filename', limit=25,
    )
    assert filtered.total == 1
    assert 'local-119.epub' in filtered.cases[0].label
    assert session.summary()['status'] == 'complete'
    assert audit.redacted()['inventory']['files'] == 126
    session.close()


@pytest.mark.parametrize('kwargs', [
    {'offset': -1}, {'limit': 0}, {'limit': 51},
    {'query': None}, {'query': 'PRIVATE' * 35},
    {'media_format': 'numilog'}, {'sha_state': 'unknown'},
    {'epub_crc': 'private'}, {'sort': 'secret'},
])
def test_untrusted_filters_are_rejected_without_query_logging(
    six_numilog, kwargs,
):
    root, database = six_numilog
    _audit, session = _session({"alice": (root,)}, database)
    with pytest.raises(ValueError, match='invalid_inspection_page'):
        session.unconfirmed_files(**kwargs)
    assert 'PRIVATE' not in repr(session)
    session.close()


def test_in_memory_filter_is_no_io_after_evidence_snapshot(
    six_numilog, monkeypatch,
):
    root, database = six_numilog
    _audit, session = _session({"alice": (root,)}, database)
    def cannot_open(*_args, **_kwargs):
        raise AssertionError('unexpected filesystem scan during file filtering')
    monkeypatch.setattr(os, 'open', cannot_open)
    selected = session.unconfirmed_files(
        query='titre cache 5', limit=25, sort='title',
        media_format='epub', sha_state='verified', epub_crc='confirmed',
    )
    assert selected.total == 1
    assert 'Titre caché 5' in selected.cases[0].label
    session.close()
