"""Numilog recovery failures survive the real canonical writer and reader."""
import logging

import pytest

from cifereca.core.diagnostic_journal import query_journal
from cifereca.core.numilog_materialization_checkpoint import NumilogIndexResumeRejected
from cifereca.diagnostic_task_context import diagnostic_task_scope
from tests.test_core_p1_3133_canonical_journal import _canonical_writer
from tests.test_numilog_owned_library_download import case


@pytest.mark.parametrize("level", ["errors", "normal", "verbose", "trace"])
@pytest.mark.parametrize("failure", ["refused", "exception"])
def test_index_failure_is_persisted_searchable_and_correlated_at_every_level(
    case, tmp_path, monkeypatch, level, failure,
):
    delivered = case.service.acquire_direct_item(case.item)
    receipt_id = delivered.metadata["numilog_checkpoint_id"]
    before_bytes = delivered.path.read_bytes()
    needle = "synthetic_index_refusal" if failure == "refused" else "synthetic SQLite index failure"
    def refuse(*_a, **_k):
        if failure == "exception":
            raise RuntimeError(needle + " " + str(delivered.path))
        return {"status": "invalid", "reason": needle, "path": str(delivered.path)}
    monkeypatch.setattr(case.service, "catalog_index_path", refuse)
    monkeypatch.setattr("cifereca.logging_utils._diagnostic_level", level)
    log_path = tmp_path / "cifereca.log"
    handler = _canonical_writer(log_path)
    logger = logging.getLogger("cifereca")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        with pytest.raises(NumilogIndexResumeRejected) as raised:
            with diagnostic_task_scope():
                case.service.resume_numilog_materialization(receipt_id)
        handler.flush()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        handler.close()
    expected = "index_rejected" if failure == "refused" else "indexing_failed"
    assert raised.value.reason_code == expected
    assert needle not in str(raised.value)  # The dialog uses the closed code.
    result = query_journal(log_path, provider_id="numilog", search=needle)
    assert result["total"] == 1  # A physical failure is recorded once.
    event, = result["events"]
    assert event["operation"] == "index_resume" and event["status"] == "error"
    assert event["detail"]["code"] == expected and event["detail"]["phase"] == "index"
    assert event["detail"]["task_kind"] == "index"
    assert event["detail"]["acquisition_id"] == receipt_id
    assert len(event["task_ref"]) == 12
    if failure == "refused":
        assert event["detail"]["index_result"]["path"] == str(delivered.path)
    else:
        assert needle in event["message"] and str(delivered.path) in event["message"]
    terminal = query_journal(log_path, search=f"task_ref={event['task_ref']} status=failed")
    assert terminal["total"] == 1
    assert log_path.read_text().count("DIAG_JSON ") == 1
    assert delivered.path.read_bytes() == before_bytes
    assert case.service.storage.acquisition_get(receipt_id)["status"] == "attention_required"
    assert case.calls == [case.item.record_id]
