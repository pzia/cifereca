"""Synthetic tests: never substitute these fixtures for private GTK receipts."""
import json
from pathlib import Path
import sys

import pytest

E2E = Path(__file__).resolve().parents[1] / "e2e"
sys.path.insert(0, str(E2E))
try:
    import notice_c_private_final_receipt as final
finally:
    sys.path.remove(str(E2E))

HEAD = "a" * 40


@pytest.fixture
def matrix(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(final, "clean_head", lambda: HEAD)
    paths = tuple(tmp_path / name for name in
                  ("generic", "generic-review", "bnf", "bnf-review"))
    for path in paths:
        path.mkdir()
    data = dict(status="PASS", git_sha=HEAD, local_c_stage="complete",
                checks=dict.fromkeys(final.ABA_MARKERS, True),
                notice_c_native_lease_audit_count=12,
                notice_c_native_late_aba_count=12,
                notice_c_cover_metrics_offline={"n4": {
                    "ui_stale": 12, "ui_failed": 0, "ui_applied": 24,
                }})
    reports = []
    for theme, width in final.MATRIX:
        report = paths[0] / theme / f"book-detail-{width}" / "screen.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps(data))
        reports.append(report)
    return paths, reports


def receipt(status, count):
    return dict(status=status, git_sha=HEAD, capture_count=count, fingerprint="f" * 64)


def stub_validators(monkeypatch):
    monkeypatch.setattr(final.generic, "verify_review",
                        lambda *_: receipt("CHECKLIST_VERIFIED", 128))
    monkeypatch.setattr(final.bnf, "verify",
                        lambda *_: receipt("BNF_CHECKLIST_VERIFIED", 96))


def test_reconciles_only_closed_receipts_at_one_head(matrix, monkeypatch):
    paths, _ = matrix
    stub_validators(monkeypatch)
    out = final.verify_matrix(*paths)
    assert out == dict(status="PRIVATE_CHECKLISTS_RECONCILED", git_sha=HEAD,
                       capture_count=224, native_aba_surfaces_per_configuration=12,
                       generic_fingerprint="f" * 64, bnf_fingerprint="f" * 64)
    assert str(paths[0]) not in repr(out)


@pytest.mark.parametrize("field,value", [
    ("git_sha", "b" * 40), ("status", "REVIEW_REQUIRED"),
    ("capture_count", 95), ("capture_count", 96.0), ("fingerprint", "PRIVATE"),
])
def test_refuses_incompatible_review_receipt(matrix, monkeypatch, field, value):
    paths, _ = matrix
    stub_validators(monkeypatch)
    bad = receipt("BNF_CHECKLIST_VERIFIED", 96)
    bad[field] = value
    monkeypatch.setattr(final.bnf, "verify", lambda *_: bad)
    with pytest.raises(final.CaptureError):
        final.verify_matrix(*paths)


@pytest.mark.parametrize("field,value", [
    ("git_sha", "b" * 40), ("notice_c_native_late_aba_count", 11),
    ("notice_c_native_lease_audit_count", 12.0), ("checks", {}),
    ("notice_c_cover_metrics_offline", {"n4": {"ui_stale": 11,
                                               "ui_failed": 0, "ui_applied": 24}}),
])
def test_one_incomplete_native_configuration_refuses_matrix(matrix, monkeypatch, field, value):
    paths, reports = matrix
    stub_validators(monkeypatch)
    data = json.loads(reports[-1].read_text())
    data[field] = value
    reports[-1].write_text(json.dumps(data))
    with pytest.raises(final.CaptureError):
        final.verify_matrix(*paths)


def test_refuses_mutated_report_or_checkout(matrix, monkeypatch):
    paths, reports = matrix
    stub_validators(monkeypatch)

    def mutate(*_):
        data = json.loads(reports[-1].read_text())
        data["changed"] = True
        reports[-1].write_text(json.dumps(data))
        return receipt("BNF_CHECKLIST_VERIFIED", 96)

    monkeypatch.setattr(final.bnf, "verify", mutate)
    with pytest.raises(final.CaptureError):
        final.verify_matrix(*paths)
    stub_validators(monkeypatch)
    heads = iter((HEAD, "b" * 40))
    monkeypatch.setattr(final, "clean_head", lambda: next(heads))
    with pytest.raises(final.CaptureError):
        final.verify_matrix(*paths)


def test_refuses_ci_and_aliasing_private_roots(matrix, monkeypatch):
    paths, _ = matrix
    stub_validators(monkeypatch)
    with pytest.raises(final.CaptureError):
        final.verify_matrix(paths[0], paths[0], paths[2], paths[3])
    monkeypatch.setenv("CI", "true")
    with pytest.raises(final.CaptureError):
        final.verify_matrix(*paths)
