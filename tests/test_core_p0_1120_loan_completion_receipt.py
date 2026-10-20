"""#1120 P0: preserve exact success ledger shapes for direct and ADEPT loans."""
from __future__ import annotations

from types import SimpleNamespace as NS
import ast
from pathlib import Path

from cifereca.core.loan_completion_receipt import completed_receipt


def _receipt(tmp_path, *, is_acsm, force_refresh, previous):
    path = tmp_path / ("file.epub" if not is_acsm else "file.pdf")
    artifact = tmp_path / "preserved.epub"
    if previous:
        artifact.write_bytes(b"old")
    ledger = {
        "entitlement_id": "e1", "provider_id": "paris",
        "credential_id": "c1", "account_id": "primary",
        "identity_id": "i1", "item_id": "id1", "title": "title",
    }
    scan = {"artifact_id": 44, "local": True}
    decision = NS(as_dict=lambda: {"source": "existing"})
    binding = {"profile": "p1", "return_available": True}
    original = dict(ledger)
    result = completed_receipt(
        ledger_base=ledger, scan=scan,
        acquisition_kind="acsm" if is_acsm else "file",
        final_path=path, loan=NS(id="loan-1"),
        force_refresh=force_refresh, decision=decision,
        previous_path=artifact if previous else None,
        adept_binding=binding if is_acsm else None,
        artifact_drm="adobe" if is_acsm else "unknown",
        artifact_format="PDF" if is_acsm else None,
        artifact_protected=True if is_acsm else None,
        hook_info={"status": "done"} if is_acsm else None,
        final_inspection=NS(media_format="EPUB", protected=False),
    )
    assert ledger == original and scan == {"artifact_id": 44, "local": True}
    return result, path, artifact, binding, scan


def test_direct_receipt_preserves_nullable_adept_fields_and_provider_inspection(tmp_path):
    result, path, artifact, binding, scan = _receipt(
        tmp_path, is_acsm=False, force_refresh=False, previous=True,
    )
    assert result == {
        "entitlement_id": "e1", "provider_id": "paris",
        "credential_id": "c1", "account_id": "primary",
        "identity_id": "i1", "item_id": "id1", "title": "title",
        "status": "complete", "strategy": "loan_auto",
        "artifact_id": 44, "executor": "provider",
        "metadata": {
            "path": str(path), "catalog_index": scan,
            "provider_loan_id": "loan-1", "reacquisition": False,
            "acquisition_decision": {"source": "existing"},
            "preserved_artifact_path": None,
            "adept_binding": None, "post_processed": None,
            "adept_artifact_drm": None, "artifact_format": "EPUB",
            "artifact_protected": False, "hook_reported": None,
        },
    }


def test_adept_receipt_preserves_binding_and_old_file_only_when_reacquiring(tmp_path):
    result, path, previous, binding, scan = _receipt(
        tmp_path, is_acsm=True, force_refresh=True, previous=True,
    )
    assert result["executor"] == "kobo-adept"
    meta = result["metadata"]
    assert meta["preserved_artifact_path"] == str(previous)
    assert meta["adept_binding"] is binding
    assert meta["post_processed"] is True
    assert meta["adept_artifact_drm"] == "adobe"
    assert meta["artifact_format"] == "PDF"
    assert meta["artifact_protected"] is True
    assert meta["hook_reported"] is True
    assert meta["reacquisition"] is True
    assert not path.exists()  # Projection never publishes/stages the artifact


def test_reacquiring_with_missing_historical_file_does_not_invent_preservation(tmp_path):
    result, *_ = _receipt(tmp_path, is_acsm=True, force_refresh=True, previous=False)
    assert result["metadata"]["preserved_artifact_path"] is None


def test_library_service_still_records_then_cleans_up_then_diagnoses():
    path = Path(__file__).parents[1] / "src" / "cifereca" / "services.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    service = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name == "LibraryService")
    method = next(node for node in service.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "_acquire_active_loan_automatically_guarded")
    calls = []
    for node in ast.walk(method):
        if isinstance(node, ast.Call):
            try:
                calls.append((ast.unparse(node.func), node.lineno))
            except Exception:
                pass
    def at(name):
        rows = [line for callee, line in calls if callee == name]
        assert rows, name
        return min(rows)
    assert at("loan_provider_delivery.index_local_loan_artifact") < (
        at("loan_completion_receipt.completed_receipt")
        < at("self._cleanup_acsm_after_success")
        < at("self._diagnose_adept_binding")
    )
    assert at("self.storage.acquisition_put") < at("self._cleanup_acsm_after_success")
