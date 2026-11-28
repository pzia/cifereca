from io import BytesIO
from zipfile import ZipFile
from hashlib import sha256
import json
import pytest

from cifereca.core import provider_health_bundle as bundle
from cifereca.core.capture_vault import VaultError


def test_bundle_manifest_and_exact_bytes(monkeypatch):
    monkeypatch.setattr(bundle, "core_contract_kit", lambda: {"plugin-dev-kit/test.txt": b"contract"})
    payload = bundle.build_health_bundle((("search", b"first"), ("detail", b"second")),
                                         include_binary=False)
    with ZipFile(BytesIO(payload)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["provider_source"] == "not_included"
        assert manifest["business_comparison"] == "not_attested"
        assert manifest["include_binary"] is False
        for row, expected in zip(manifest["captures"], (b"first", b"second")):
            assert archive.read(row["member"]) == expected
            assert row["sha256"] == sha256(expected).hexdigest()
        assert archive.read("plugin-dev-kit/test.txt") == b"contract"
        assert archive.testzip() is None
    with pytest.raises(VaultError):
        bundle.build_health_bundle((("download", b"x"),), include_binary=False)
    with pytest.raises(VaultError):
        bundle.build_health_bundle((), include_binary=False)


def test_bundle_reports_observed_version_without_attesting_source(monkeypatch):
    monkeypatch.setattr(bundle, "core_contract_kit", lambda: {})
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("index.json", json.dumps({"health_observation": {
            "schema": "cifereca.health-observation/1", "capability": "search",
            "version": "2.1", "observed_at": "2026-11-28T12:00:00+00:00",
            "outcome": "success"}}))
    payload = bundle.build_health_bundle((("search", stream.getvalue()),), include_binary=False)
    with ZipFile(BytesIO(payload)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["captures"][0]["observed_provider_version"] == "2.1"
        assert manifest["provider_source"] == "not_included"
        assert manifest["provider_version"] == "not_attested_in_bundle"


def test_correlated_journal_exact_provider_and_reference(monkeypatch, tmp_path):
    from cifereca.core import diagnostic_journal as journal
    ref = "123456abcdef"
    terminal = {"task_ref": ref, "logger": "cifereca", "severity": "info",
                "message": f"TRACE task correlation task_ref={ref} status=success"}
    exact = {"task_ref": ref, "provider_id": "fixture", "logger": "cifereca",
             "severity": "info", "message": "exact"}
    unrelated = dict(exact, task_ref="000000000000")
    wrong = dict(exact, provider_id="other")
    free_text = dict(terminal, message="not an exact terminal line")
    monkeypatch.setattr(journal, "_events", lambda path: (
        [terminal, exact, unrelated, wrong, free_text], [], False))
    result = journal.provider_health_correlated_journal(
        tmp_path / "log", provider_id="fixture", evidence_refs=(ref,))
    assert result["events"] == [terminal, exact]
    assert result["coverage"] == "retained_matches"
    monkeypatch.setattr(bundle, "core_contract_kit", lambda: {})
    payload = bundle.build_health_bundle((("search", b"fixture"),),
                                         include_binary=False, correlated_journal=result)
    with ZipFile(BytesIO(payload)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["correlated_logs"] == "retained_matches"
        assert json.loads(archive.read("journal/correlated.json")) == result
        assert archive.testzip() is None


def test_installed_source_snapshot_excludes_non_source(monkeypatch, tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace as NS
    from cifereca.core.provider_health_source import snapshot_provider_source
    root = tmp_path / "package"
    root.mkdir()
    (root / "cifereca-plugin.json").write_text("{}")
    (root / "parser.py").write_text("x = 1")
    (root / "config.json").write_text("private")
    record = NS(descriptor=NS(id="fixture", type="provider.library", version="2"),
                package=NS(root=root, sha256="unknown", manifest=NS(package=NS(version="2"))))
    manager = NS(_records={"fixture": record}, hold_descriptive_lifecycle=nullcontext)
    snapshot = snapshot_provider_source(manager, "fixture")
    assert set(snapshot["files"]) == {"cifereca-plugin.json", "parser.py"}
    monkeypatch.setattr(bundle, "core_contract_kit", lambda: {})
    payload = bundle.build_health_bundle((("search", b"fixture"),),
                                         include_binary=False, provider_source=snapshot)
    with ZipFile(BytesIO(payload)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["provider_source"] == "current_installed_sources"
        assert manifest["provider_version"] == "2"
        assert archive.read("provider-source/parser.py") == b"x = 1"
        assert "provider-source/config.json" not in archive.namelist()
    (root / "unsafe.py").symlink_to(root / "config.json")
    assert snapshot_provider_source(manager, "fixture") is None
