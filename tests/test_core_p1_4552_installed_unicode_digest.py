"""#4552: malformed installed Unicode must fail before capture or journal."""
from dataclasses import replace

import pytest

from cifereca.storage import Storage
from cifereca.core.notice_descriptive_core_coordinator import run_descriptive_core_job
from tests.test_core_p1_4981_consolidator_worker import DECLARATION, SCHEMA, TARGET, installed


@pytest.mark.parametrize("bad_source", ["\ud800", "\udfff", "def run():\n    x = '\ud800'"])
def test_installed_surrogate_rejected_before_capture(tmp_path, bad_source):
    storage = Storage(tmp_path / "invalid-source.sqlite")
    calls = []
    def capture(db):
        calls.append(True)
        return (), {}
    with pytest.raises(ValueError, match="^installed_consolidator_mismatch$"):
        run_descriptive_core_job(
            storage, capture_sources_txn=capture,
            declarations=(DECLARATION,), text_schemas=(SCHEMA,),
            installed=replace(installed(), source=bad_source),
            canonical_type="age", scope="fr:editions",
            selected_plugin_id="plugin.age.alpha", selected_version="1.0.0",
            target_edition_id=TARGET, now=100, lease_seconds=60,
        )
    assert calls == []
