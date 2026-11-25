"""Exact public-age producer uses authoritative current journal generation."""
from contextlib import closing

import pytest

from cifereca.storage import Storage
from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_c_public_age_producer import (
    request_current_public_age, request_current_public_age_txn,
)
from cifereca.core.notice_descriptive_config_owner import configure_descriptive_selection
from tests.test_core_p0_4530_public_age_c import original, project
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist
from tests.test_core_p1_4981_consolidator_worker import DECLARATION, SCHEMA, installed


def test_current_producer_dedup_revision_and_restart(tmp_path):
    path = tmp_path / "producer.sqlite"
    binding = installed()
    with closing(Storage(path)) as storage:
        source = persist(storage, project(original()), at=100)
        target = source.observation.key.edition_id
        args = dict(target_edition_id=target, scope="fr:editions", now=100,
                    implementation_digests=(binding.source_sha256,))
        assert request_current_public_age(storage, **args) is None
        configure_descriptive_selection(
            storage, canonical_type="age", scope=args["scope"],
            selected_plugin_id=DECLARATION.plugin_id, selected_version=DECLARATION.version,
            declarations=(DECLARATION,), text_schemas=(SCHEMA,),
            installed_registry=(binding,), expected_generation=0,
        )
        assert request_current_public_age(storage, **args) is None
        persist_exact_public_search_cover(storage, source)
        wrong = "0"*64 if binding.source_sha256 != "0"*64 else "1"*64
        assert request_current_public_age(storage, **(args | {
            "implementation_digests": (wrong,),
        })) is None
        ref = request_current_public_age(storage, **args)
        assert ref is not None
        assert request_current_public_age(storage, **args) == ref
        assert journal.read_job(storage, ref).attempts == 0
    with closing(Storage(path)) as storage:
        assert request_current_public_age(storage, **args) == ref
        changed = original()
        changed.metadata["bibliography_evidence"]["youth_age"]["ranges"][0]["age_min"] = 14
        newer = persist(storage, project(changed), at=101)
        persist_exact_public_search_cover(storage, newer)
        next_ref = request_current_public_age(storage, **(args | {"now": 101}))
        assert next_ref.key == ref.key and next_ref.generation > ref.generation
        assert journal.read_job(storage, next_ref).attempts == 0
        assert storage.entitlements() == storage.acquisitions() == []


def test_producer_requires_transaction_and_nonempty_trusted_filter(tmp_path):
    with closing(Storage(tmp_path / "producer.sqlite")) as storage:
        args = dict(target_edition_id="edition:1", scope="fr:editions", now=100,
                    implementation_digests=())
        with storage._lock, storage._connect() as sql:
            with pytest.raises(ValueError, match="transaction_required"):
                request_current_public_age_txn(sql, **args)
        with pytest.raises(ValueError, match="invalid_public_age_filter"):
            request_current_public_age(storage, **args)


def test_current_producer_preserves_explicit_origin_locator(tmp_path):
    from tests.test_core_p1_4552_public_age_inheritance import setup

    with closing(Storage(tmp_path / "origin-producer.sqlite")) as storage:
        group, sources = setup(storage, tmp_path)
        binding = installed()
        target = sources[0].observation.key.edition_id
        args = dict(target_edition_id=target, scope="fr:editions", now=300,
                    implementation_digests=(binding.source_sha256,))
        configure_descriptive_selection(
            storage, canonical_type="age", scope=args["scope"],
            selected_plugin_id=DECLARATION.plugin_id, selected_version=DECLARATION.version,
            declarations=(DECLARATION,), text_schemas=(SCHEMA,),
            installed_registry=(binding,), expected_generation=0,
        )
        persist_exact_public_search_cover(storage, sources[0])
        with pytest.raises(ValueError, match="invalid_descriptive_spec"):
            request_current_public_age(storage, **args, group_document="invalid")
        ref = request_current_public_age(storage, **args, group_document=group.document)
        assert ref is not None
        assert request_current_public_age(storage, **args, group_document=group.document) == ref
        assert request_current_public_age(storage, **args) is None
        job = journal.read_job(storage, ref)
        assert job.spec.public_age_locator == (target, args["scope"], group.document.source_key)
        assert job.attempts == 0
        with storage._lock, storage._connect() as sql:
            sql.execute("DELETE FROM origin_links")
        with pytest.raises(ValueError, match="relation_changed|origin_group_required"):
            request_current_public_age(storage, **args, group_document=group.document)
        assert journal.read_job(storage, ref) == job
        assert storage.entitlements() == storage.acquisitions() == []
