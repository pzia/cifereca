"""#4552 Q7: actual canonical capture journal -> Diagnostics presentation."""
import logging

import pytest

from cifereca import logging_utils
from cifereca.diagnostics import Diagnostics
from cifereca.core.diagnostic_journal import query_journal
from cifereca.core.notice_parse_witness_alert import report_capture_diagnostic
from diagnostics_explorer import notice_capture_label, sanitized_rows


def test_capture_events_present_closed_state_from_real_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "trace")
    path = tmp_path / "cifereca.log"
    handler = logging_utils.PrivateRotatingFileHandler(
        path, maxBytes=1000000, backupCount=3, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"
    ))
    logger = logging.getLogger("cifereca")
    old = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        diagnostics = Diagnostics(level="trace")
        for state, diagnostic in (
            ("complete", "a"*64), ("partial", "b"*64),
            ("failed", "c"*64), ("capacity_refused", None),
        ):
            report_capture_diagnostic(diagnostics, diagnostic, state, rejected=True)
        report_capture_diagnostic(diagnostics, "c"*64, "partial")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)
        handler.close()
    payload = query_journal(path, module="NOTICE")
    rows = sanitized_rows(payload, include_detail=True)
    assert len(rows) == 5
    assert {row["message"] for row in rows} == {
        "Parsing : régression possible · archive créée",
        "Parsing : régression possible · archive partielle",
        "Parsing : régression possible · échec de création de l’archive",
        "Parsing : régression possible · capacité de capture atteinte",
        "Parsing : reprise de capture · archive partielle",
    }
    assert all(set(row["detail"]) == {"diagnostic_id", "capture_state"} for row in rows)
    assert len(sanitized_rows(query_journal(path, search="parser_witness_rejected"))) == 4


@pytest.mark.parametrize("change", [
    {"source": "app_log"}, {"module": "PLUGIN"}, {"logger": "plugin.other"},
    {"message": "foreign"}, {"operation": "foreign"}, {"status": "ok"},
    {"detail": {"diagnostic_id": "private-title", "capture_state": "partial"}},
    {"detail": {"diagnostic_id": "a"*64, "capture_state": "unknown"}},
    {"detail": {"diagnostic_id": "a"*64, "capture_state": "partial", "account": "private"}},
])
def test_nonmatching_event_does_not_get_capture_label(change):
    event = dict(source="journal", module="NOTICE", logger="cifereca",
                 message="parser_witness_rejected", operation="parser_witness_rejected",
                 status="warning", severity="warning",
                 detail={"diagnostic_id": "a"*64, "capture_state": "partial"})
    event.update(change)
    assert notice_capture_label(event) is None
