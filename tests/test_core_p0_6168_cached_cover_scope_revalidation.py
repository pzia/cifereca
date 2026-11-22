"""#6168/#2385: cached N2 reads never trust mutated public scopes."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.gui import notice_cover_cached_reader as reader


def public_scope():
    return CoverDocumentScope(
        kind="public_catalogue", identity_id=None,
        source_key="notice-c:v1:public-fixture", provider_id="fixture",
        credential_id="__public__",
    )


@pytest.mark.parametrize("field,value", [
    ("credential_id", "private-credential"),
    ("identity_id", "identity-A"),
    ("account_id", "account-A"),
    ("provider_id", ""),
    ("source_key", ""),
    ("loan_id", "loan-A"),
    ("artifact_id", "artifact-A"),
    ("placement_id", "placement-A"),
])
def test_mutated_public_scope_is_refused_before_sqlite(monkeypatch, field, value):
    scope = public_scope()
    object.__setattr__(scope, field, value)
    calls = []
    monkeypatch.setattr(reader, "is_canonical_cover_document",
                        lambda *_: calls.append("canonical") or False)
    monkeypatch.setattr(reader, "read_current_notice_cover",
                        lambda *_: calls.append("sqlite") or None)
    assert reader.cached_notice_cover_snapshot(NS(storage=object()), scope) is None
    assert calls == []


def test_valid_public_scope_still_reaches_original_read_path(monkeypatch):
    scope = public_scope()
    calls = []
    monkeypatch.setattr(reader, "is_canonical_cover_document",
                        lambda *_: calls.append("canonical") or False)
    monkeypatch.setattr(reader, "read_current_notice_cover",
                        lambda *_: calls.append("sqlite") or None)
    assert reader.cached_notice_cover_snapshot(NS(storage=object()), scope) is None
    assert calls == ["canonical", "sqlite"]


def test_valid_canonical_scope_preserves_canonical_read_path(monkeypatch):
    scope = public_scope()
    calls = []
    monkeypatch.setattr(reader, "is_canonical_cover_document",
                        lambda *_: calls.append("canonical") or True)
    monkeypatch.setattr(reader, "read_current_consolidated_public_cover",
                        lambda *_: calls.append("sqlite") or None)
    assert reader.cached_notice_cover_snapshot(NS(storage=object()), scope) is None
    assert calls == ["canonical", "sqlite"]
