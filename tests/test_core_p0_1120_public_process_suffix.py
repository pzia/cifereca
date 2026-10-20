"""P0 #1120: same bounded ADEPT failure suffix, without workflow effects."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cifereca.core.loan_failure_diagnostics import failure_fields
from cifereca.core.loan_failure_evidence import public_process_suffix


def test_exact_legacy_public_counter_order_and_spacing():
    fields = failure_fields(
        code="adept_error", loan_ref=None,
        process={
            "artifact_present": False, "stderr_bytes": 1024,
            "returncode": 10, "artifact_bytes": 42,
            "stdout_bytes": 12, "duration_ms": 35,
            "stdout_summary": "PRIVATE_XML", "private_path": "/PRIVATE/file.acsm",
        },
    )
    assert public_process_suffix(fields) == (
        " (returncode=10, duration_ms=35, stdout_bytes=12, "
        "stderr_bytes=1024, artifact_bytes=42, artifact_present=False)"
    )
    assert "PRIVATE" not in public_process_suffix(fields)


@pytest.mark.parametrize("process", [
    {}, {"returncode": True}, {"returncode": -256},
    {"returncode": 256}, {"duration_ms": -1},
    {"duration_ms": 86_400_001}, {"stdout_bytes": -1},
    {"stderr_bytes": "PRIVATE"}, {"artifact_present": 1},
    {"error": "PRIVATE", "path": "/PRIVATE"},
])
def test_no_unattested_process_fact_is_rendered(process):
    assert public_process_suffix(process) == ""


def test_empty_or_missing_facts_do_not_modify_generic_failure_message():
    assert public_process_suffix(
        failure_fields(code="adept_error", loan_ref=None, process={})
    ) == ""
    assert public_process_suffix(None) == ""


def test_never_inspects_hostile_payload_object_or_stringifies_hidden_fields():
    class Hostile:
        def __str__(self):
            raise AssertionError("unexpected private __str__")
        def __bool__(self):
            raise AssertionError("unexpected private __bool__")
    fields = {
        "returncode": Hostile(), "artifact_present": Hostile(),
        "stderr": Hostile(), "code": Hostile(),
    }
    assert public_process_suffix(fields) == ""
    assert public_process_suffix(Hostile()) == ""


def test_service_keeps_classification_before_persisted_receipt_and_response():
    source = Path(__file__).resolve().parents[1] / "src/cifereca/services.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    service = next(n for n in tree.body
                   if isinstance(n, ast.ClassDef) and n.name == "LibraryService")
    method = next(n for n in service.body
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_acquire_active_loan_automatically_guarded")
    handlers = [
        n for n in ast.walk(method) if isinstance(n, ast.ExceptHandler)
        and any(isinstance(c, ast.Call)
                and ast.unparse(c.func) == "loan_failure_evidence.public_process_suffix"
                for c in ast.walk(n))
    ]
    assert len(handlers) == 1
    handler = handlers[0]
    def lines(name):
        return sorted(n.lineno for n in ast.walk(handler) if isinstance(n, ast.Call)
                      and ast.unparse(n.func) == name)
    assert len(lines("failure_fields")) >= 2
    assert len(lines("loan_failure_evidence.public_process_suffix")) == 1
    assert len(lines("self.storage.acquisition_put")) == 1
    assert len(lines("loan_failure_evidence.adept_failure_outcome")) == 1
    assert (lines("loan_failure_evidence.public_process_suffix")[0] <
            lines("self.storage.acquisition_put")[0] <
            lines("loan_failure_evidence.adept_failure_outcome")[0])
