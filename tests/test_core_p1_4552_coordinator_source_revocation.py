"""#4552: source/proof changes between isolated execution and SQL CAS fail closed."""
from dataclasses import replace

import pytest

from cifereca.storage import Storage
from cifereca.core import notice_descriptive_runner_adapter as adapter
from cifereca.core.notice_descriptive_core_coordinator import run_descriptive_core_job
from cifereca.core.notice_descriptive_materialization_store import read_descriptive_materialization
from tests.test_core_p1_4981_consolidator_worker import (
    DECLARATION, SCHEMA, TARGET, FOREIGN, fact, installed,
)


@pytest.mark.parametrize("after", [
    (fact("14+"),),
    (),
    (fact("13+", edition=FOREIGN),),
])
def test_source_revocation_during_worker_cannot_publish(tmp_path, monkeypatch, after):
    storage = Storage(tmp_path / "revoked.sqlite")
    state = {"facts": (fact(),)}
    def capture(db):
        return state["facts"], {}
    real = adapter.propose_registered_descriptive_materialization
    def revoke_after_worker(**kwargs):
        result = real(**kwargs)
        state["facts"] = after
        return result
    monkeypatch.setattr(
        "cifereca.core.notice_descriptive_core_coordinator."
        "propose_registered_descriptive_materialization",
        revoke_after_worker,
    )
    with pytest.raises(ValueError, match="stale_descriptive_materialization"):
        run_descriptive_core_job(
            storage, capture_sources_txn=capture,
            declarations=(DECLARATION,), text_schemas=(SCHEMA,),
            installed=installed(), canonical_type="age", scope="fr:editions",
            selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
            target_edition_id=TARGET, now=100, lease_seconds=60,
        )
    # Even after restart the failed proposal has no persisted row.
    assert read_descriptive_materialization(
        Storage(tmp_path / "revoked.sqlite"),
        target_edition_id=TARGET, canonical_type="age", scope="fr:editions",
        capture_current_txn=lambda db: (None, None, None),
    ) is None
