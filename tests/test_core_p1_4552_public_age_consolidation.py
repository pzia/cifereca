"""#4552: real public age job entry point binds the SQL capture contract."""
from unittest.mock import patch

from cifereca.core.notice_c_public_age_consolidation import (
    run_public_age_descriptive_job,
)


def test_public_age_job_supplies_exact_sql_capture_to_coordinator():
    from cifereca.core.notice_c_public_age_sql_snapshot import (
        capture_public_age_sources_txn,
    )
    captured = {}

    def fake_run(storage, **kwargs):
        captured.update(kwargs)
        return "published"

    with patch(
        "cifereca.core.notice_c_public_age_consolidation.run_descriptive_core_job",
        side_effect=fake_run,
    ):
        result = run_public_age_descriptive_job(
            object(), source_documents=("doc:1",),
            declarations=(), text_schemas=(), installed=None,
            scope="public", selected_plugin_id="plugin.age",
            selected_version="1.0", target_edition_id="edition:1",
            now=100, lease_seconds=30,
        )
    assert result == "published"
    assert captured["canonical_type"] == "age"
    assert captured["target_edition_id"] == "edition:1"
    assert captured["capture_sources_txn"] is not None
    with patch(
        "cifereca.core.notice_c_public_age_consolidation.capture_public_age_sources_txn",
        return_value=(({"edition_id": "edition:1"},), {}),
    ) as reader:
        assert captured["capture_sources_txn"](object()) == (
            ({"edition_id": "edition:1"},), {},
        )
    assert reader.call_args.kwargs["documents"] == ("doc:1",)
    assert reader.call_args.kwargs["target_edition_id"] == "edition:1"


def test_public_age_job_empty_inventory_abstains():
    assert run_public_age_descriptive_job(
        object(), source_documents=(), declarations=(), text_schemas=(),
        installed=None, scope="public", selected_plugin_id="plugin.age",
        selected_version="1.0", target_edition_id="edition:1",
        now=100, lease_seconds=30,
    ) is None
