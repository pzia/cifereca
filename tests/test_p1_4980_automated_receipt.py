"""Synthetic receipt is not a human review."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))
import notice_c_automated_receipt as receipt

SHA = "a" * 40


@pytest.fixture
def matrix(monkeypatch):
    generic = Mock(return_value=([{}] * 128, "b" * 64))
    bnf = Mock(return_value=([{}] * 96, "c" * 64))
    aba = Mock()
    monkeypatch.setattr(receipt.generic, "verify_captures", generic)
    monkeypatch.setattr(receipt.bnf, "inventory", bnf)
    monkeypatch.setattr(receipt, "native_aba_report", aba)
    monkeypatch.setattr(receipt, "clean_head", lambda: SHA)
    return SimpleNamespace(generic=generic, bnf=bnf, aba=aba)


def test_closed_receipt(tmp_path, matrix):
    result = receipt.verify(tmp_path, SHA)
    assert result == dict(status="SYNTHETIC_GTK_MATRIX_VERIFIED", git_sha=SHA,
                          capture_count=224, native_aba_surfaces_per_configuration=12,
                          generic_fingerprint="b" * 64, bnf_fingerprint="c" * 64,
                          human_visual_review=False)
    assert matrix.aba.call_count == 4
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("family,count", [("generic", 127), ("bnf", 95)])
def test_missing_capture_refused(tmp_path, matrix, family, count):
    getattr(matrix, family).return_value = ([{}] * count, "d" * 64)
    with pytest.raises(receipt.CaptureError):
        receipt.verify(tmp_path, SHA)


def test_aba_failure_refused(tmp_path, matrix):
    matrix.aba.side_effect = receipt.CaptureError("invalid native report")
    with pytest.raises(receipt.CaptureError):
        receipt.verify(tmp_path, SHA)


def test_head_changed_refused(tmp_path, matrix, monkeypatch):
    monkeypatch.setattr(receipt, "clean_head", lambda: "f" * 40)
    with pytest.raises(receipt.CaptureError):
        receipt.verify(tmp_path, SHA)
    with pytest.raises(receipt.CaptureError):
        receipt.run(sys.executable, SHA)
