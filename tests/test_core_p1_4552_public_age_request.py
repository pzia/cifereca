"""#4552 exact public age durable request, dedup and reconstruction."""
from contextlib import closing
from dataclasses import replace
import pytest

from cifereca.storage import Storage
from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_descriptive_config_owner import configure_descriptive_selection
from cifereca.core.notice_c_public_age_request import (
    request_public_age_consolidation, reattest_public_age_job_txn,
)
from tests.test_core_p0_4530_public_age_c import original, project
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist
from tests.test_core_p1_4981_consolidator_worker import DECLARATION, SCHEMA, installed


def test_exact_c_age_request_recovers_after_restart_and_fences_revision(tmp_path):
    path = tmp_path / "age-job.sqlite"
    scope = "fr:editions"
    with closing(Storage(path)) as db:
        source = persist(db, project(original()), at=100)
        target = source.observation.key.edition_id
        args = dict(target_edition_id=target, scope=scope, now=100, expected_generation=0)
        assert request_public_age_consolidation(db, **args) is None
        schema = replace(SCHEMA, max_bytes=2048)
        configure_descriptive_selection(
            db, canonical_type="age", scope=scope,
            selected_plugin_id=DECLARATION.plugin_id, selected_version=DECLARATION.version,
            declarations=(DECLARATION,), text_schemas=(schema,),
            installed_registry=(installed(),), expected_generation=0,
        )
        assert request_public_age_consolidation(db, **args) is None  # no self C
        persist_exact_public_search_cover(db, source)
        ref = request_public_age_consolidation(db, **args)
        assert ref is not None
        spec = journal.read_job(db, ref).spec
        assert spec.public_age_locator == (target, scope)
        assert journal.read_job(db, ref).attempts == 0
        assert request_public_age_consolidation(db, **args) == ref
    with closing(Storage(path)) as db:
        assert journal.read_job(db, ref).spec == spec
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN")
            pin, admitted = reattest_public_age_job_txn(sql, spec)
            assert pin.selection.plugin_id == DECLARATION.plugin_id
            assert admitted.exact and not admitted.inherited
        changed = original()
        changed.metadata["bibliography_evidence"]["youth_age"]["ranges"][0]["age_min"] = 14
        newer = persist(db, project(changed), at=101)
        persist_exact_public_search_cover(db, newer)
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN")
            with pytest.raises(ValueError, match="public_age_input_changed"):
                reattest_public_age_job_txn(sql, spec)
        next_ref = request_public_age_consolidation(
            db, **(args | {"now": 101, "expected_generation": ref.generation}),
        )
        assert next_ref.key == ref.key and next_ref.generation > ref.generation
        assert db.entitlements() == db.acquisitions() == []


def test_age_locator_is_bound_to_exact_journal_target():
    spec = journal.descriptive_job_spec(
        target_edition_id="edition:one", canonical_type="age", scope="fr:editions",
        input_digest="a"*64, implementation_digest="b"*64, public_age=True,
    )
    for locator in (("edition:other", "fr:editions"), ("edition:one", "foreign"), ["edition:one", "fr:editions"]):
        with pytest.raises(ValueError, match="invalid_descriptive"):
            journal._check_spec(replace(spec, public_age_locator=locator))
    legacy = journal.descriptive_job_spec(
        target_edition_id="edition:one", canonical_type="age", scope="fr:editions",
        input_digest="a"*64, implementation_digest="b"*64,
    )
    assert legacy.public_age_locator is None
    assert journal._key(spec) == journal._key(legacy)
