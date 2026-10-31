"""#3882: acquisition-audit CLI is read-only and source-local."""
import json
import os

from cifereca import cli
from cifereca.core import acquisition_audit
from tests.test_core_p0_3719_sync_cli_auto_select import make_cli_core


def test_cli_audit_unique_source_requires_no_network(monkeypatch, tmp_path, capsys):
    observed = make_cli_core(monkeypatch, [
        ("private-profile", "numilog", "private-card", "private-buyer", True),
        ("other-profile", "numilog", "other-card", "other-buyer", True),
    ])
    scanned = []
    def audit(storage, account, **kw):
        scanned.append((account.identity_id, account.provider_id, account.credential_id))
        assert kw["verify_files"] is False
        return {"schema_version": 1, "scope": "exact_source", "rights": [], "receipts": []}
    monkeypatch.setattr(acquisition_audit, "inspect_exact_source", audit)
    target = tmp_path / "audit.json"
    assert cli.main([
        "acquisition-audit", "--provider", "numilog", "--identity", "private-profile",
        "--json", "--output", str(target),
    ]) == 0
    assert scanned == [("private-profile", "numilog", "private-card")]
    assert observed["connects"] == 0 and not observed["runs"]
    assert os.stat(target).st_mode & 0o777 == 0o600
    assert json.loads(target.read_text())["scope"] == "exact_source"
    assert json.loads(capsys.readouterr().out)["output"] == "created"


def test_cli_audit_ambiguity_and_absence_refuse_network(monkeypatch, capsys):
    observed = make_cli_core(monkeypatch, [
        ("private-profile", "numilog", "card-A", "buyer-A", True),
        ("private-profile", "numilog", "card-B", "buyer-B", True),
    ])
    assert cli.main([
        "acquisition-audit", "--identity", "private-profile",
        "--provider", "numilog", "--json",
    ]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "account_ambiguous"
    assert cli.main([
        "acquisition-audit", "--identity", "unknown-profile",
        "--provider", "numilog", "--json",
    ]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "source_not_configured"
    assert observed["connects"] == 0 and not observed["runs"]


def test_existing_audit_refused_before_opening_core(monkeypatch, tmp_path, capsys):
    target = tmp_path / "audit.json"
    target.write_bytes(b"PRIVATE_KEEP")
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **kw: (_ for _ in ()).throw(AssertionError("must be offline preflight")),
    )
    assert cli.main([
        "acquisition-audit", "--identity", "any",
        "--output", str(target),
    ]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "audit_destination_exists"
    assert target.read_bytes() == b"PRIVATE_KEEP"


def test_atomic_output_rejects_symlink_and_does_not_overwrite(tmp_path):
    target = tmp_path / "private"
    target.write_bytes(b"private")
    alias = tmp_path / "audit.json"
    alias.symlink_to(target)
    import pytest
    with pytest.raises(FileExistsError):
        acquisition_audit.write_acquisition_audit(alias, {"status": "success"})
    assert alias.is_symlink() and target.read_bytes() == b"private"
