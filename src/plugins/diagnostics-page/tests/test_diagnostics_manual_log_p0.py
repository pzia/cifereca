"""Offline security contracts for the user-selected diagnostics log projection."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / (
    "src/plugins/diagnostics-page/diagnostics_manual_log.py"
)
spec = importlib.util.spec_from_file_location("diagnostics_manual_log", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def _dump(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def test_explicit_regular_file_projects_only_fixed_tokens(tmp_path):
    text = (
        "2026-10-11 WARNING ADEPT retry bearer=very-private-token\n"
        "Camille Dupont alice@example.org /home/camille/private.log\n"
        "2026-10-11 ERROR KOBO timeout https://host.invalid/?sig=ABC\n"
    )
    file = tmp_path / "Alice-Dupont-private.log"
    file.write_text(text, encoding="utf-8")
    projection = module.read_selected_log(str(file))
    public = module.public_selected_log_evidence(projection)
    assert public["source"] == "user_selected_local_log"
    assert public["sampled_lines"] == 3
    assert public["matched_lines"] == 2
    assert public["events"] == [
        {"sample_id": "manual-01", "severity": "warning",
         "component": "adept", "operation": "retry"},
        {"sample_id": "manual-02", "severity": "error",
         "component": "kobo", "operation": "timeout"},
    ]
    serialized = _dump(public)
    for forbidden in (
        "Camille", "Dupont", "alice@", "/home", "private.log",
        "very-private-token", "host.invalid", "ABC",
    ):
        assert forbidden not in serialized


def test_oversize_files_sample_only_tail_and_disclose_partial(tmp_path):
    file = tmp_path / "huge.log"
    file.write_bytes(b"ERROR ADEPT retry Alice.secret\n" * 3000
                     + b"WARNING SYNC failed Bob.private\n")
    public = module.read_selected_log(str(file))
    assert public["truncated"] is True
    assert public["coverage"] == "partial_sample"
    assert public["sampled_lines"] <= module.MAX_LINES
    assert 1 <= public["matched_lines"] <= module.MAX_LINES
    assert len(public["events"]) <= module.MAX_EVENTS
    assert "Alice" not in _dump(public) and "Bob" not in _dump(public)


def test_invalid_utf8_and_binary_are_rejected_without_raw_error(tmp_path):
    file = tmp_path / "broken.log"
    for raw in (b"INFO\xff\n", b"ERROR\x00Kobo\n"):
        file.write_bytes(raw)
        with pytest.raises(ValueError, match="selected_log_invalid_encoding"):
            module.read_selected_log(str(file))


def test_special_file_symlink_and_absent_path_fail_closed(tmp_path):
    file = tmp_path / "ok.log"
    file.write_text("ERROR KOBO", encoding="utf-8")
    link = tmp_path / "symlink.log"
    link.symlink_to(file)
    with pytest.raises(ValueError, match="selected_log_unavailable"):
        module.read_selected_log(str(link))
    with pytest.raises(ValueError, match="selected_log_not_regular"):
        module.read_selected_log(str(tmp_path))
    with pytest.raises(ValueError, match="selected_log_unavailable"):
        module.read_selected_log(str(tmp_path / "not_there"))
    with pytest.raises(ValueError, match="selected_log_invalid"):
        module.read_selected_log(None)


def test_projection_rejects_free_form_text_path_and_forged_enums():
    good = module.summarize_selected_log_bytes(b"ERROR ADEPT timeout\n")
    for field, value in (
        ("path", "/home/Camille"),
        ("message", "Alice Dupont"),
        ("url", "https://example.invalid/?secret=token"),
    ):
        tampered = {**good, field: value}
        with pytest.raises(ValueError, match="selected_log_unsafe"):
            module.public_selected_log_evidence(tampered)
    for field, value in (
        ("severity", "Alice Dupont"), ("component", "token=SECRET"),
        ("operation", "HTTP private.com"), ("sample_id", "personal"),
    ):
        tampered = {**good, "events": [{**good["events"][0], field: value}]}
        with pytest.raises(ValueError, match="selected_log_unsafe"):
            module.public_selected_log_evidence(tampered)
    assert module.public_selected_log_evidence(good) == good


def test_invalid_summary_types_are_not_stringified():
    good = module.summarize_selected_log_bytes(b"WARNING SYNC retry")
    for field, value in (
        ("sampled_lines", True), ("matched_lines", "1"),
        ("truncated", "false"), ("events", ["secret"]),
        ("coverage", "Alex@example.org"),
    ):
        with pytest.raises(ValueError, match="selected_log_unsafe"):
            module.public_selected_log_evidence({**good, field: value})


def test_lines_are_not_kept_and_empty_sample_has_no_invented_anomaly():
    public = module.summarize_selected_log_bytes(
        "Alice Dupont wrote a private note".encode("utf-8"),
    )
    assert public["sampled_lines"] == 1
    assert public["matched_lines"] == 0
    assert public["events"] == []
    assert "Alice" not in _dump(public)
    assert module.summarize_selected_log_bytes(b"")["events"] == []
