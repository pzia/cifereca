"""Recommendation retains Search's exact documentary facts, without actions."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import CatalogBrokerImpl
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_c_recommendation_known import known_public_c_binding_facts
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


def _seed(db):
    row = verified_public(title="Les sciences connues")
    base = {"source": row.source_provider + ".fixture", "scope": row.scenario,
            "freshness": "search_response"}
    row.metadata = {
        "series": ["Les sciences"], "languages": ["fr", "en"],
        "bibliography_evidence": {
            "series": {**base, "source_field": "Search.series", "confidence": "linked_search_relation"},
            "languages": {**base, "raw_tags": ["fra", "eng"], "source_field": "dc.language",
                          "confidence": "explicit_record_field"},
        },
    }
    source = persist(db, row)
    c = persist_exact_public_search_cover(db, source)
    assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    return c, row.metadata


def test_retained_metadata_reaches_real_recommendation_broker_after_restart(tmp_path):
    path = tmp_path / "recommendation.sqlite"
    db = Storage(path)
    try:
        c, expected = _seed(db)
    finally:
        db.close()
    db = Storage(path)
    try:
        item, = CatalogBrokerImpl(NS(storage=db)).known_public_notices("sciences")
        assert item.metadata == {"notice_c_known": True, "notice_c_stale": True, **expected}
        assert known_public_c_binding_facts(item) == (c.document, c.epoch)
        assert known_public_c_binding_facts(replace(item)) is None
        assert item.source_credential == "" and item.source_account_id is None
        assert item.owned is item.downloadable is item.download_url is None
        assert item.friendly_url is item.thumbnail_url is None
        original = deepcopy(item.metadata)
        item.metadata["bibliography_evidence"]["languages"]["raw_tags"].append("spa")
        assert known_public_c_binding_facts(item) is None
        item.metadata = deepcopy(original)
        assert known_public_c_binding_facts(item) == (c.document, c.epoch)
        item.metadata["series"][0] = "Une autre série"
        assert known_public_c_binding_facts(item) is None
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


class CallbackBomb:
    def __eq__(self, other): raise AssertionError("untrusted equality")
    def __str__(self): raise AssertionError("untrusted string")
    def __deepcopy__(self, memo): raise AssertionError("untrusted deepcopy")


class ListBomb(list):
    def __iter__(self): raise AssertionError("untrusted iterator")


@pytest.mark.parametrize("poison", [
    CallbackBomb(), [CallbackBomb()], ListBomb(["fr"]), "x" * 8193,
    ["fr"] * 33, {str(i): "x" for i in range(65)},
])
def test_mutated_metadata_never_runs_callbacks_or_outgrows_receipt(tmp_path, poison):
    db = Storage(tmp_path / "hostile.sqlite")
    try:
        _seed(db)
        item, = CatalogBrokerImpl(NS(storage=db)).known_public_notices("sciences")
        item.metadata["languages"] = poison
        assert known_public_c_binding_facts(item) is None
    finally:
        db.close()


def test_cyclic_metadata_is_refused_with_bounded_traversal(tmp_path):
    db = Storage(tmp_path / "cycle.sqlite")
    try:
        _seed(db)
        item, = CatalogBrokerImpl(NS(storage=db)).known_public_notices("sciences")
        cycle = []
        cycle.append(cycle)
        item.metadata["languages"] = cycle
        assert known_public_c_binding_facts(item) is None
    finally:
        db.close()


@pytest.mark.parametrize("field", [
    "source_credential", "source_collection", "title", "source_provider", "description",
])
def test_non_native_dto_field_is_rejected_before_any_equality(tmp_path, field):
    db = Storage(tmp_path / "dto.sqlite")
    try:
        _seed(db)
        item, = CatalogBrokerImpl(NS(storage=db)).known_public_notices("sciences")
        setattr(item, field, CallbackBomb())
        assert known_public_c_binding_facts(item) is None
    finally:
        db.close()
