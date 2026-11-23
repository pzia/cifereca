"""#4552: untrusted installed plugin identity cannot enqueue durable jobs."""
from dataclasses import replace

import pytest

from cifereca.storage import Storage
from cifereca.core.notice_descriptive_core_coordinator import run_descriptive_core_job
from tests.test_core_p1_4981_consolidator_worker import (
    DECLARATION, SCHEMA, TARGET, fact, installed,
)


@pytest.mark.parametrize("binding", [
    replace(installed(), plugin_id="another.plugin"),
    replace(installed(), scope="another.scope"),
    replace(installed(), schema_id="schema.other"),
    replace(installed(), source_sha256="0" * 64),
    object(),
])
def test_invalid_installed_binding_rejected_before_source_capture(tmp_path, binding):
    storage = Storage(tmp_path / "invalid.sqlite")
    calls = []
    def forbidden_capture(db):
        calls.append(True)
        return (fact(),), {}
    with pytest.raises(ValueError, match="installed_consolidator_mismatch"):
        run_descriptive_core_job(
            storage, capture_sources_txn=forbidden_capture,
            declarations=(DECLARATION,), text_schemas=(SCHEMA,),
            installed=binding, canonical_type="age", scope="fr:editions",
            selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
            target_edition_id=TARGET, now=100, lease_seconds=60,
        )
    assert calls == []
