"""#3882: standalone audit file and CLI output share a closed safe DTO."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from cifereca import cli
from cifereca.core import acquisition_audit
from tests.test_core_p0_3719_sync_cli_auto_select import make_cli_core


def private_injected_audit():
    return {
        "schema_version": 1, "scope": "exact_source",
        "network": "not_used", "mutations": "none",
        "verify_files": False,
        "credentials": {"token": "PRIVATE_TOKEN"},
        "raw_error": "PRIVATE_EXCEPTION",
        "rights": [
            {
                "resource_ref": "a" * 64, "kind": "purchase",
                "right_state": "active", "classification": "source_unlinked",
                "origin_link_count": 0, "placement_count": 0,
                "file_verification": "not_requested",
                "title": "PRIVATE_TITLE", "path": "/PRIVATE/PATH",
            },
            {
                "resource_ref": "PRIVATE_REFERENCE",
                "kind": "PRIVATE_KIND", "right_state": "PRIVATE_STATE",
                "classification": "PRIVATE_CLASSIFICATION",
                "credential": "PRIVATE_COOKIE",
            },
        ],
        "receipts": [
            {"receipt_ref": "b" * 64,
             "status": "canonical_receipt_staged",
             "classification": "staged_unpublished",
             "signed_url": "PRIVATE_SIGNED_URL"},
        ],
        "staging_evidence": {
            "coverage": "indexed_account_receipts_only",
            "staged_verified": 1,
            "staged_visible_in_library": 0,
            "observations": [
                {"receipt_ref": "b" * 64, "staging_state": "verified_staging_file",
                 "staging_sha_verified": True, "path": "/PRIVATE/STAGING",
                 "cookie": "PRIVATE_COOKIE"},
            ],
            "session": "PRIVATE_SESSION",
        },
    }


def test_standalone_writer_keeps_canonical_evidence_without_free_fields(tmp_path):
    out = tmp_path / "audit.json"
    original = private_injected_audit()
    acquisition_audit.write_acquisition_audit(out, original)
    text = out.read_text(encoding="ascii")
    result = json.loads(text)
    assert (os.stat(out).st_mode & 0o777) == 0o600
    assert result["schema_version"] == 1
    assert result["scope"] == "exact_source"
    assert result["rights"][0]["resource_ref"] == "a" * 64
    assert result["rights"][0]["classification"] == "source_unlinked"
    assert result["rights"][1]["classification"] == "unclassified"
    assert "resource_ref" not in result["rights"][1]
    assert result["receipts"][0]["status"] == "canonical_receipt_staged"
    assert result["staging_evidence"]["observations"][0]["staging_sha_verified"] is True
    assert result["staging_evidence"]["staged_verified"] == 1
    for secret in (
        "PRIVATE_TOKEN", "PRIVATE_EXCEPTION", "PRIVATE_TITLE",
        "PRIVATE_REFERENCE", "PRIVATE_COOKIE", "PRIVATE_SIGNED_URL",
        "PRIVATE_SESSION", "PRIVATE_KIND", "PRIVATE_STATE",
        "PRIVATE_CLASSIFICATION", "/PRIVATE/",
    ):
        assert secret not in text
    assert original["rights"][0]["title"] == "PRIVATE_TITLE"  # no mutation


@pytest.mark.parametrize("malformed", [
    None, {}, {"schema_version": True, "scope": "exact_source"},
    {"schema_version": 2, "scope": "exact_source"},
    {"schema_version": 1, "scope": "PRIVATE_SCOPE"},
])
def test_invalid_root_does_not_create_standalone_audit(tmp_path, malformed):
    target = tmp_path / "invalid.json"
    with pytest.raises(ValueError, match="acquisition_audit_invalid_result"):
        acquisition_audit.write_acquisition_audit(target, malformed)
    assert not target.exists()


@pytest.mark.parametrize("to_file", [False, True])
def test_cli_projects_mocked_audit_before_file_or_stdout(
    monkeypatch, tmp_path, capsys, to_file,
):
    observed = make_cli_core(monkeypatch, [
        ("profile-a", "numilog", "card-a", "account-a", True),
    ])
    monkeypatch.setattr(
        acquisition_audit, "inspect_exact_source",
        lambda *_args, **_kw: private_injected_audit(),
    )
    args = ["acquisition-audit", "--provider", "numilog",
            "--identity", "profile-a", "--json"]
    path = tmp_path / "audit.json"
    if to_file:
        args.extend(["--output", str(path)])
    assert cli.main(args) == 0
    output = json.loads(capsys.readouterr().out)
    if to_file:
        assert output == {"status": "success", "output": "created"}
        audit = json.loads(path.read_text(encoding="ascii"))
        printable = path.read_text(encoding="ascii")
    else:
        assert output["status"] == "success"
        audit = output["audit"]
        printable = json.dumps(output)
        assert not path.exists()
    assert audit["rights"][0]["resource_ref"] == "a" * 64
    assert audit["rights"][0]["classification"] == "source_unlinked"
    assert audit["staging_evidence"]["staged_verified"] == 1
    assert "PRIVATE_" not in printable
    assert "/PRIVATE/" not in printable
    assert observed["connects"] == 0 and observed["runs"] == []


def test_cli_rejects_invalid_root_without_false_success_or_file(
    monkeypatch, tmp_path, capsys,
):
    make_cli_core(monkeypatch, [
        ("profile-a", "numilog", "card-a", "account-a", True),
    ])
    monkeypatch.setattr(acquisition_audit, "inspect_exact_source",
                        lambda *_args, **_kw: {"schema_version": 1, "scope": "spoofed",
                                               "token": "PRIVATE_TOKEN"})
    path = tmp_path / "invalid.json"
    rc = cli.main([
        "acquisition-audit", "--provider", "numilog",
        "--identity", "profile-a", "--json", "--output", str(path),
    ])
    assert rc == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed", "code": "audit_unavailable",
    }
    assert not path.exists()
