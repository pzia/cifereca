"""#3882: exact CLI-bound Core events and closed A/B report projection."""
import json
import logging
import threading
from types import SimpleNamespace
import zipfile

from cifereca import cli
from cifereca.core.diagnostic_source_sync import CANONICAL_EXECUTION_STATUSES
from cifereca.core.sync_cli_contract import safe_sync_result
from cifereca.core.sync_report import collect_source_sync_core_events, write_sync_report
from cifereca.diagnostics import Diagnostics
from tests.test_core_p0_3719_sync_cli_auto_select import make_cli_core


_ACCOUNT = SimpleNamespace(
    identity_id="PRIVATE_PROFILE", provider_id="kobo",
    credential_id="PRIVATE_CREDENTIAL", unique_id="PRIVATE_ACCOUNT",
)
_EXECUTION = {name: 0 for name in CANONICAL_EXECUTION_STATUSES}
_EXECUTION.update({"pending": 8, "present_attested": 1})


def _read(path):
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        result = json.loads(archive.read("sync-result.json"))
        events = [json.loads(line) for line in archive.read("events.jsonl").splitlines()]
        contents = b"".join(archive.read(name) for name in archive.namelist())
    return manifest, result, events, contents


def test_cli_projection_keeps_real_counts_without_provider_payload():
    raw = {
        "status": "partial", "canonical_execution": dict(_EXECUTION),
        "canonical_rights": {"status": "partial", "observed": 9,
                             "attested_active": 9, "pending": 8,
                             "api_token": "PRIVATE_SECRET"},
        "loans": 9, "library": 1,
        "signed_url": "PRIVATE_SIGNED_URL",
    }
    safe = safe_sync_result(raw)
    assert safe["canonical_execution"]["pending"] == 8
    assert safe["canonical_execution"]["failed"] == 0
    assert safe["canonical_rights"] == {
        "status": "partial", "observed": 9, "attested_active": 9,
        "pending": 8,
    }
    assert safe["loans"] == 9
    assert "PRIVATE_" not in str(safe)
    malformed = safe_sync_result({
        "status": "partial", "canonical_execution": {
            **_EXECUTION, "pending": True,
        }, "canonical_rights": {"observed": 1_000_001, "pending": False},
    })
    assert "canonical_execution" not in malformed
    assert "canonical_rights" not in malformed


def test_scoped_observer_rejects_other_provider_thread_and_raw_log(caplog):
    diag = Diagnostics()
    counts = {"canonical_execution": dict(_EXECUTION), "loans": 9}
    with caplog.at_level(logging.INFO, logger="cifereca"):
        with collect_source_sync_core_events("kobo") as observed:
            diag.add_core_source_sync(
                "kobo", phase="completed", status="partial",
                code="source_sync_partial", counts=counts,
            )
            diag.add_core_source_sync(
                "numilog", phase="completed", status="partial",
                code="source_sync_partial", counts=counts,
            )
            logging.getLogger("cifereca").info(
                "TRACE source sync provider_id=kobo status=failed "
                "PRIVATE_SIGNED_URL"
            )
            thread = threading.Thread(target=lambda: diag.add_core_source_sync(
                "kobo", phase="completed", status="partial",
                code="source_sync_partial", counts=counts,
            ))
            thread.start()
            thread.join(timeout=3)
    assert len(observed) == 1
    assert observed[0]["canonical_pending"] == 8
    assert observed[0]["canonical_failed"] == 0
    assert observed[0]["provider_id"] == "kobo"


def test_zip_core_coverage_and_partial_reason_are_exact(tmp_path):
    diag = Diagnostics()
    with collect_source_sync_core_events("kobo") as observed:
        # Make the Core INFO event visible without changing normal writer
        # behavior or invoking any supplier.
        logger = logging.getLogger("cifereca")
        before = logger.level
        logger.setLevel(logging.INFO)
        try:
            diag.add_core_source_sync(
                "kobo", phase="completed", status="partial",
                code="source_sync_partial",
                counts={"canonical_execution": dict(_EXECUTION), "loans": 9},
            )
        finally:
            logger.setLevel(before)
    target = tmp_path / "core.zip"
    write_sync_report(
        target, account=_ACCOUNT, run_ref="a" * 32,
        before={}, after={}, started_at="start", exit_code=2,
        result={"status": "partial", "canonical_execution": dict(_EXECUTION),
                "canonical_rights": {"status": "partial", "observed": 9}},
        cli_events=[{"phase": "sync", "status": "partial"}],
        core_events=observed,
    )
    manifest, result, events, contents = _read(target)
    assert manifest["event_coverage"] == "cli_and_core_source_sync"
    assert manifest["core_source_sync_events"] == 1
    assert result["code"] == "source_sync_partial"
    assert result["canonical_execution"]["pending"] == 8
    assert result["canonical_execution"]["failed"] == 0
    core = [e for e in events if e.get("origin") == "core_source_sync"]
    assert len(core) == 1
    assert core[0]["run_ref"] == manifest["run_ref"]
    assert core[0]["canonical_present_attested"] == 1
    assert core[0]["canonical_pending"] == 8
    for token in (b"PRIVATE_PROFILE", b"PRIVATE_CREDENTIAL", b"PRIVATE_ACCOUNT"):
        assert token not in contents


def test_cli_exact_run_integrates_core_event_and_stays_source_scoped(
    monkeypatch, tmp_path, capsys,
):
    observed = make_cli_core(monkeypatch, [
        ("profile-a", "kobo", "credential-a", "account-a", True),
    ])
    core = cli.CiferecaCore.open()
    diag = Diagnostics()

    def sync(account):
        observed["runs"].append(account)
        diag.add_core_source_sync(
            "kobo", phase="completed", status="partial",
            code="source_sync_partial", counts={
                "canonical_execution": dict(_EXECUTION), "loans": 9,
            },
        )
        return {"status": "partial", "canonical_execution": dict(_EXECUTION),
                "canonical_rights": {"status": "partial", "observed": 9},
                "loans": 9, "url": "PRIVATE_SIGNED_URL"}

    core.library.synchronize_account_source = sync
    target = tmp_path / "cli.zip"
    with _LoggerInfo():
        assert cli.main([
            "sync", "run", "--provider", "kobo", "--identity", "profile-a",
            "--confirm", "--report", str(target),
        ]) == 2
    assert json.loads(capsys.readouterr().out)["report"] == "created"
    manifest, result, events, contents = _read(target)
    assert manifest["event_coverage"] == "cli_and_core_source_sync"
    assert result["canonical_execution"]["pending"] == 8
    assert result["code"] == "source_sync_partial"
    assert len([x for x in events if x.get("origin") == "core_source_sync"]) == 1
    assert b"PRIVATE_" not in contents


class _LoggerInfo:
    def __enter__(self):
        self.logger = logging.getLogger("cifereca")
        self.level = self.logger.level
        self.logger.setLevel(logging.INFO)

    def __exit__(self, *_args):
        self.logger.setLevel(self.level)
