"""P0 #5054: pure private batch shape guard never attests ownership itself."""
from dataclasses import replace
import hashlib

import pytest

from cifereca.models import SearchResult
from cifereca.core.private_inventory_row_scope import (
    MAX_PRIVATE_ROWS, private_inventory_rows_match_scope,
)
from cifereca.core.numilog_private_rows import private_library_rows_valid


def _row(provider="fixture-store", credential="cred", collection="library",
         scenario="FIXTURE_PRIVATE", record="scoped:edition"):
    return SearchResult(
        id=record, record_id=record, docbase="FIXTURE", medium="digital",
        title="Fictional book", author=None, type=None, format=None,
        publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario=scenario,
        source_provider=provider, source_credential=credential,
        source_collection=collection, owned=True,
    )


def _valid(rows, **kw):
    args = dict(provider_id="fixture-store", credential_id="cred",
                collection="library", scenario="FIXTURE_PRIVATE",
                scoped_record_valid=lambda rid: rid.startswith("scoped:"))
    args.update(kw)
    return private_inventory_rows_match_scope(rows, **args)


def test_unknown_private_provider_shape_checked_without_provider_allowlist():
    assert _valid([_row()])
    assert not _valid([_row(), _row(provider="foreign")])
    assert not _valid([_row(), _row(credential="other")])
    assert not _valid([_row(), _row(collection="catalogue")])
    assert not _valid([_row(), _row(scenario="PUBLIC")])
    assert not _valid([_row(), replace(_row(), owned=1)])
    assert not _valid([_row(), replace(_row(), id="other")])
    assert not _valid([_row(), replace(_row(), record_id="unscoped")])


def test_invalid_source_or_record_validator_fails_closed():
    assert not _valid([_row()], credential_id="")
    assert not _valid([_row()], provider_id=None)
    assert not _valid([_row()], scoped_record_valid=lambda _record: 1)
    assert not _valid([_row()], scoped_record_valid=lambda _record: (_ for _ in ()).throw(ValueError("private")))
    assert not _valid((_row(),))
    assert not _valid([_row()] * (MAX_PRIVATE_ROWS + 1))


def test_numilog_compatibility_requires_exact_hashed_credential_lid_and_variant():
    credential = "numilog-private"
    scoped = "numilog:" + hashlib.sha256(credential.encode()).hexdigest()[:20]
    record = scoped + ":lid-1:" + "a" * 16
    result = _row(
        provider="numilog", credential=credential,
        scenario="NUMILOG_LIBRARY", record=record,
    )
    assert private_library_rows_valid([result], credential)
    assert not private_library_rows_valid([replace(result, source_collection="catalogue")], credential)
    assert not private_library_rows_valid([replace(result, owned=False)], credential)
    assert not private_library_rows_valid([replace(result, source_credential="foreign")], credential)
    assert not private_library_rows_valid([replace(result, record_id="numilog:guess")], credential)
    assert not private_library_rows_valid([replace(result, source_provider="fixture-store")], credential)
    assert not private_library_rows_valid([result], "foreign")
