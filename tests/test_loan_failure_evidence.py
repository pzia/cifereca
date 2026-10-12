"""Characterize the existing read-only evidence without changing retry policy."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.loan_failure_evidence import (
    process_failure_facts, adept_failure_metadata, provider_failure_metadata,
)


@pytest.mark.parametrize("exit_code, expected", [(None, 10), (7, 7), ("7", 10), (True, True)])
def test_process_uses_typed_allowlist_and_keeps_exception_override(exit_code, expected):
    raw = {"returncode": 10, "duration_ms": 25, "stdout_bytes": True,
           "stderr_bytes": "100", "stdout": "PRIVATE", "token": "PRIVATE"}
    before = deepcopy(raw)
    result = process_failure_facts(raw, exc=NS(exit_code=exit_code), candidate=None, path_type=Path)
    assert result == {"returncode": expected, "duration_ms": 25}
    assert raw == before
    # The historical exception override accepts bool as int. This structural
    # extraction preserves that behavior, rather than silently changing it.
    assert type(result["returncode"]) is type(expected)


def test_artifact_probe_is_read_only_and_does_not_expose_path(tmp_path):
    file = tmp_path / "retained.epub"
    file.write_bytes(b"retained contents")
    result = process_failure_facts({}, exc=NS(), candidate=file, path_type=Path)
    assert result == {"artifact_present": True, "artifact_bytes": 17}
    assert file.read_bytes() == b"retained contents"
    assert str(file) not in repr(result)
    assert process_failure_facts({}, exc=NS(), candidate=str(file), path_type=Path) == {}


def test_exception_exit_signal_is_read_after_raw_facts_and_before_artifact_probe():
    order = []
    class Raw(dict):
        def get(self, key):
            order.append(key)
            return super().get(key)
    class Failure(Exception):
        @property
        def exit_code(self):
            order.append("exception-exit")
            return 7
    class Probe:
        def is_file(self):
            order.append("artifact-exists")
            return False
    assert process_failure_facts(Raw(), exc=Failure(), candidate=Probe(), path_type=Probe) == {
        "returncode": 7, "artifact_present": False,
    }
    assert order == ["returncode", "duration_ms", "stdout_bytes", "stderr_bytes",
                     "exception-exit", "artifact-exists"]


@pytest.mark.parametrize("failure", ["is_file", "stat"])
def test_artifact_race_is_reported_as_missing_without_suppressing_other_facts(failure):
    class Probe:
        def is_file(self):
            if failure == "is_file":
                raise OSError("vanished")
            return True
        def stat(self):
            raise OSError("vanished after existence check")
    assert process_failure_facts(
        {"returncode": 1}, exc=NS(), candidate=Probe(), path_type=Probe,
    ) == {"returncode": 1, "artifact_present": False}


@pytest.mark.parametrize("executor,profile", [("provider", "chosen"), ("kobo-adept", "chosen"),
                                              ("kobo-adept", None), ("kobo-adept", 12)])
def test_profile_evidence_only_comes_from_fulfillment_attempt(executor, profile, tmp_path):
    previous = {"path": "prior book", "adept_binding": {"origin": "original"}}
    before = deepcopy(previous)
    process = {"returncode": 10}
    retained = tmp_path / "retained.acsm"
    retained.write_bytes(b"keep")
    result = adept_failure_metadata(previous, loan_id="loan", process=process,
        retained_acsm=retained, executor=executor, attempt_profile=profile)
    assert result["provider_loan_id"] == "loan"
    assert result["process"] is process
    assert result["acsm_staging_path"] == str(retained)
    assert ("failure_stage" in result) == (executor == "kobo-adept")
    assert ("adept_attempt_profile" in result) == (executor == "kobo-adept" and isinstance(profile, str))
    assert result["adept_binding"] == previous["adept_binding"]
    assert previous == before and retained.read_bytes() == b"keep"


@pytest.mark.parametrize("validation,attention", [(False, None), (False, "provider_quota_exceeded"),
                                                  (True, None), (True, "provider_quota_exceeded")])
def test_generic_evidence_retains_provenance_and_does_not_classify_retry(validation, attention):
    previous = {"path": "previous", "adept_binding": {"profile": "historic"}}
    before = deepcopy(previous)
    result = provider_failure_metadata(previous, loan_id="current", retained_acsm=None,
        attention_code=attention, validation_failure=validation)
    assert result["provider_loan_id"] == "current"
    assert result["adept_binding"] == previous["adept_binding"]
    assert ("provider_condition" in result) == bool(attention)
    assert ("repairable" in result) == validation
    assert "acsm_staging_path" not in result
    assert "status" not in result and "retry" not in result
    assert previous == before
