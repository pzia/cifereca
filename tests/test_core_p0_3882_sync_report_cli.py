"""#3882: opt-in report lifecycle is separate from sensitive HTTP traces."""
import json
import zipfile

from cifereca import cli
from tests.test_core_p0_3719_sync_cli_auto_select import make_cli_core


def test_successful_confirmed_sync_writes_report(monkeypatch, tmp_path, capsys):
    observed = make_cli_core(monkeypatch, [
        ("profile-a", "numilog", "card-a", "account-a", True),
    ])
    path = tmp_path / "numilog-report.zip"
    rc = cli.main([
        "sync", "run", "--identity", "profile-a", "--provider", "numilog",
        "--confirm", "--report", str(path),
    ])
    assert rc == 0
    assert observed["connects"] == len(observed["runs"]) == 1
    assert json.loads(capsys.readouterr().out)["report"] == "created"
    with zipfile.ZipFile(path) as z:
        manifest = json.loads(z.read("manifest.json"))
        result = json.loads(z.read("sync-result.json"))
        assert manifest["status"] == result["status"] == "success"
        assert result["downloaded"] == 1
        assert json.loads(z.read("acquisition-audit-before.json")) == {
            "status": "unavailable", "reason": "audit_unavailable",
        }
        assert json.loads(z.read("acquisition-audit-after.json")) == {
            "status": "unavailable", "reason": "audit_unavailable",
        }


def test_existing_report_refused_before_any_core(monkeypatch, tmp_path, capsys):
    path = tmp_path / "old.zip"
    path.write_bytes(b"KEEP")
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **kw: (_ for _ in ()).throw(AssertionError("Core must not open")),
    )
    rc = cli.main([
        "sync", "run", "--identity", "profile-a", "--confirm",
        "--report", str(path),
    ])
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["code"] == "report_destination_exists"
    assert path.read_bytes() == b"KEEP"


def test_no_confirmation_keeps_destination_absent(monkeypatch, tmp_path, capsys):
    target = tmp_path / "should-not-exist.zip"
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **kw: (_ for _ in ()).throw(AssertionError("No Core before confirm")),
    )
    rc = cli.main([
        "sync", "run", "--identity", "profile-a", "--report", str(target),
    ])
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["code"] == "explicit_run_confirmation_required"
    assert not target.exists()


def test_ambiguous_selection_creates_no_report_or_network(monkeypatch, tmp_path, capsys):
    observed = make_cli_core(monkeypatch, [
        ("profile-a", "numilog", "card-a", "account-a", True),
        ("profile-a", "numilog", "card-b", "account-b", True),
    ])
    target = tmp_path / "should-not-exist.zip"
    rc = cli.main([
        "sync", "run", "--identity", "profile-a", "--provider", "numilog",
        "--confirm", "--report", str(target),
    ])
    assert rc == 2
    assert observed["connects"] == 0 and not observed["runs"]
    assert not target.exists()
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def test_failed_business_sync_still_writes_report(monkeypatch, tmp_path, capsys):
    observed = make_cli_core(monkeypatch, [
        ("profile-a", "numilog", "card-a", "account-a", True),
    ])
    # The run was admitted; a typed provider failure must still get an archive.
    target = tmp_path / "failed.zip"
    core = cli.CiferecaCore.open()
    core.library.synchronize_account_source = lambda account: {
        "status": "partial", "errors": 1,
        "secret": "PASSWORD_NOT_FOR_EXPORT",
    }
    rc = cli.main([
        "sync", "run", "--identity", "profile-a", "--provider", "numilog",
        "--confirm", "--report", str(target),
    ])
    assert rc == 2 and target.exists()
    with zipfile.ZipFile(target) as z:
        assert json.loads(z.read("manifest.json"))["status"] == "partial"
        assert b"PASSWORD_NOT_FOR_EXPORT" not in z.read("sync-result.json")
    assert json.loads(capsys.readouterr().out)["report"] == "created"



def test_connection_failure_after_selection_still_exports_report(monkeypatch, tmp_path, capsys):
    observed = make_cli_core(monkeypatch, [
        ("profile-a", "numilog", "card-a", "account-a", True),
    ])
    core = cli.CiferecaCore.open()
    def connection_failure(**_kwargs):
        observed["connects"] += 1
        raise RuntimeError("PRIVATE_AUTH_DETAILS")
    core.library.connect_selected_source = connection_failure
    target = tmp_path / "failed-before-provider-session.zip"
    rc = cli.main([
        "sync", "run", "--identity", "profile-a", "--provider", "numilog",
        "--confirm", "--report", str(target),
    ])
    assert rc == 2
    assert observed["connects"] == 1 and not observed["runs"]
    assert target.is_file()
    with zipfile.ZipFile(target) as z:
        assert json.loads(z.read("manifest.json"))["status"] == "failed"
        assert b"PRIVATE_AUTH_DETAILS" not in b"".join(
            z.read(name) for name in z.namelist()
        )
    assert json.loads(capsys.readouterr().out)["report"] == "created"



def test_raw_http_export_cannot_alias_redacted_report(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **kw: (_ for _ in ()).throw(AssertionError("No Core on conflicting exports")),
    )
    destination = tmp_path / "only-one.zip"
    rc = cli.main([
        "sync", "run", "--identity", "profile-a", "--confirm",
        "--report", str(destination),
        "--trace-export", str(destination), "--confirm-trace-export",
    ])
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["code"] == "report_trace_destination_conflict"
    assert not destination.exists()



def test_file_verification_needs_report_destination(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **kw: (_ for _ in ()).throw(AssertionError("no Core before preflight")),
    )
    assert cli.main([
        "sync", "run", "--identity", "profile-a",
        "--confirm", "--verify-files",
    ]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "report_required_for_file_verification"
