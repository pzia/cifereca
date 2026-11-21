"""P0 public C cursor validates request types before any SQLite read.

This is request-shape validation only; no provider query, entitlement A or
file/placement B is created by the positive case.
"""
from contextlib import closing

import pytest

from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


class Hostile:
    def __hash__(self):
        raise AssertionError("foreign request hash")

    def __eq__(self, other):
        raise AssertionError("foreign request equality")

    def __str__(self):
        raise AssertionError("foreign request string coercion")


class NoStorage:
    def __getattr__(self, name):
        raise AssertionError("invalid request reached SQLite: " + name)


@pytest.mark.parametrize("kwargs", [
    {"field": Hostile()}, {"scope": Hostile()},
    {"field": []}, {"scope": []},
    {"field": 1}, {"scope": 1},
    {"provider_ids": (Hostile(),)},
    {"provider_ids": ("p" * 65,)},
    {"provider_ids": "catalogue"},
    {"provider_ids": ("public",) * 129},
    {"content_types": (Hostile(),)},
    {"age": True}, {"age_kind": Hostile()},
    {"group_by_document": Hostile()},
    {"now": 2**63},
])
def test_invalid_public_c_request_refuses_before_hash_or_storage(kwargs):
    with pytest.raises(ValueError, match="invalid_notice_c_search_request"):
        search_public_notice_c(NoStorage(), "Correspondance", **kwargs)


def test_native_public_c_request_keeps_partial_source_contract(tmp_path):
    with closing(Storage(tmp_path / "native-request.sqlite")) as db:
        source = persist(db, verified_public(
            record_id="public-request", title="Correspondance catalogue",
        ))
        page = search_public_notice_c(
            db, "Correspondance", field="title", scope="all",
            provider_ids=(source.observation.key.provider_id,), now=100,
        )
        assert page.status == "partial"
        assert page.known_matches == 1
        assert page.hits[0].record_id == "public-request"
        assert page.hits[0].stale is False
        assert db.entitlements() == db.acquisitions() == db.placements() == []
