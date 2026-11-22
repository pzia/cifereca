"""#4761: mutable frozen Core C sources cannot change exact edition/observation."""
from contextlib import closing

import pytest

from cifereca.core.notice_c_recommendation_known import (
    known_public_notice_candidates, known_public_c_binding_facts,
    known_public_c_primary_hit, known_public_c_alternative_hits,
)
from cifereca.storage import Storage
from tests.test_core_p0_1023_grouped_c_source_filters import make, shared


@pytest.mark.parametrize("field,value", [
    ("edition_id", "other-edition"), ("observed_at", 1),
])
def test_primary_unprojected_coordinates_remain_exact(tmp_path, field, value):
    with closing(Storage(tmp_path / "primary-exact.sqlite")) as db:
        first = make(db, "bnf", "a", "Deux sources A", "Astronomie", "fr", "EPUB")
        second = make(db, "numilog-catalog", "b", "Deux sources B", "Cuisine", "en", "PDF")
        canonical = shared(db, first, second)
        candidate, = known_public_notice_candidates(db, "Deux sources")
        original = candidate._core_known_public_c_projection[6]
        assert known_public_c_binding_facts(candidate) == (canonical.document, canonical.epoch)
        assert known_public_c_primary_hit(candidate) is original
        assert getattr(original, field) != value
        object.__setattr__(original, field, value)
        assert known_public_c_binding_facts(candidate) == (canonical.document, canonical.epoch)
        assert known_public_c_primary_hit(candidate) is None
        assert db.entitlements() == [] and db.acquisitions() == []


@pytest.mark.parametrize("field,value", [
    ("edition_id", "alternative-other-edition"), ("last_seen_at", 2**62),
])
def test_alternative_unprojected_coordinates_remain_exact(tmp_path, field, value):
    with closing(Storage(tmp_path / "alt-exact.sqlite")) as db:
        first = make(db, "bnf", "a", "Deux sources A", "Astronomie", "fr", "EPUB")
        second = make(db, "numilog-catalog", "b", "Deux sources B", "Cuisine", "en", "PDF")
        canonical = shared(db, first, second)
        candidate, = known_public_notice_candidates(db, "Deux sources")
        alternative, = known_public_c_alternative_hits(candidate)
        assert known_public_c_primary_hit(candidate) is not None
        assert getattr(alternative, field) != value
        object.__setattr__(alternative, field, value)
        assert known_public_c_binding_facts(candidate) == (canonical.document, canonical.epoch)
        assert known_public_c_alternative_hits(candidate) is None
        assert known_public_c_primary_hit(candidate) is not None
        assert db.entitlements() == [] and db.acquisitions() == []
