from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import zipfile

import pytest

from cifereca.plugin_api import (
    ArtifactFormat,
    ArtifactRef,
    LibraryReadBroker,
    MaintenanceContext,
    MetadataBroker,
    PluginContext,
    PluginDirectories,
)

import cifereca_library_audits as audits
from cifereca_library_audits import IntegrityAuditTool


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_valid_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0"></container>',
        )
        archive.writestr("EPUB/content.xhtml", "<html><body>OK</body></html>")


class FakeMetadataBroker:
    def __init__(self, entries):
        self._entries = [dict(row) for row in entries]

    def library_entries(self, **options):
        identity_id = options.get("identity_id")
        return [
            dict(row)
            for row in self._entries
            if identity_id is None or row.get("identity_id") == identity_id
        ]


class FakeLibraryReadBroker:
    def __init__(self, artifacts, failing_ids=()):
        self._artifacts = dict(artifacts)
        self._failing_ids = {str(value) for value in failing_ids}

    def artifact(self, artifact_id):
        if str(artifact_id) in self._failing_ids:
            raise RuntimeError("library.read unavailable")
        return self._artifacts.get(str(artifact_id))


class Control:
    def __init__(self):
        self.events = []

    def cancelled(self):
        return False

    def check_cancelled(self):
        return None

    def report(self, **event):
        self.events.append(dict(event))


def test_integrity_audit_through_public_brokers_is_read_only():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good_epub = root / "good.epub"
        bad_epub = root / "broken.epub"
        bad_pdf = root / "broken.pdf"
        missing = root / "missing.epub"

        write_valid_epub(good_epub)
        bad_epub.write_bytes(b"not-a-zip")
        bad_pdf.write_bytes(b"not a pdf")

        paths = [good_epub, bad_epub, bad_pdf, missing]
        formats = ["epub", "epub", "pdf", "epub"]
        ids = ["a-good", "a-bad-epub", "a-bad-pdf", "a-missing"]

        artifacts = {}
        entries = []
        for artifact_id, path, media_format in zip(ids, paths, formats):
            exists = path.exists()
            size = path.stat().st_size if exists else 321
            digest = sha256(path) if exists else "f" * 64
            artifacts[artifact_id] = ArtifactRef(
                artifact_id=artifact_id,
                book_id=None,
                format=ArtifactFormat(
                    media_type="application/epub+zip" if media_format == "epub" else "application/pdf",
                    format=media_format,
                ),
                size_bytes=size,
                sha256=digest,
            )
            entries.append({
                "artifact_id": artifact_id,
                "identity_id": "id1",
                "title": artifact_id,
                "local": True,
                "local_path": str(path),
                "media_format": media_format,
            })

        # Force independent size/hash findings on the malformed PDF.
        artifacts["a-bad-pdf"] = ArtifactRef(
            artifact_id="a-bad-pdf",
            book_id=None,
            format=ArtifactFormat(media_type="application/pdf", format="pdf"),
            size_bytes=bad_pdf.stat().st_size + 5,
            sha256="0" * 64,
        )

        metadata: MetadataBroker = FakeMetadataBroker(entries)
        library_read: LibraryReadBroker = FakeLibraryReadBroker(artifacts)

        context = PluginContext(
            plugin_id="maintenance.audit.integrity",
            package_id="org.cifereca.maintenance.library-audit",
            directories=PluginDirectories(data=root / "data", cache=root / "cache"),
            _broker_resolver=lambda broker_id: {
                "metadata": metadata,
                "library.read": library_read,
            }.get(broker_id),
        )

        tool = IntegrityAuditTool().activate(context)
        plan = tool.plan(MaintenanceContext(scope="library", identity_id="id1"))
        assert len(plan.facts["candidates"]) == 4

        control = Control()
        result = tool.run(plan, control)

        assert result.ok is False
        assert result.changed == 0
        assert result.facts["checked"] == 4
        assert result.facts["invalid"] == 3
        assert result.facts["missing"] == 1
        assert result.facts["issues"]["container_invalid"] == 1
        assert result.facts["issues"]["pdf_header"] == 1
        assert result.facts["issues"]["size_mismatch"] == 1
        assert result.facts["issues"]["sha256_mismatch"] == 1
        assert result.facts["issues"]["missing"] == 1
        assert control.events[-1]["message"] == "Audit d’intégrité terminé"
        # Result warnings are also displayed/exportable diagnostics: use
        # positions and issue codes, never private filenames or titles.
        assert all(name not in "\\n".join(result.warnings)
                   for name in ("good.epub", "broken.epub", "broken.pdf", "missing.epub"))
        assert any("Fichier 2/4 : container_invalid" in w for w in result.warnings)
        assert any("Fichier 4/4 : missing" in w for w in result.warnings)

        # The read fakes expose no mutation method: any attempted write fails immediately.
        assert not any(
            name.startswith(("put", "update", "delete", "write", "mutate"))
            for name in dir(metadata) + dir(library_read)
        )


def test_integrity_audit_reports_incomplete_reference_reads():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        epub = root / "good.epub"
        write_valid_epub(epub)
        artifact_id = "a-good"
        artifact = ArtifactRef(
            artifact_id=artifact_id,
            book_id=None,
            format=ArtifactFormat(media_type="application/epub+zip", format="epub"),
            size_bytes=epub.stat().st_size,
            sha256=sha256(epub),
        )
        metadata: MetadataBroker = FakeMetadataBroker([{
            "artifact_id": artifact_id,
            "identity_id": "id1",
            "title": "Good",
            "local": True,
            "local_path": str(epub),
            "media_format": "epub",
        }])
        library_read: LibraryReadBroker = FakeLibraryReadBroker(
            {artifact_id: artifact},
            failing_ids={artifact_id},
        )
        context = PluginContext(
            plugin_id="maintenance.audit.integrity",
            package_id="org.cifereca.maintenance.library-audit",
            directories=PluginDirectories(data=root / "data", cache=root / "cache"),
            _broker_resolver=lambda broker_id: {
                "metadata": metadata,
                "library.read": library_read,
            }.get(broker_id),
        )

        tool = IntegrityAuditTool().activate(context)
        plan = tool.plan(MaintenanceContext(scope="library", identity_id="id1"))
        assert plan.facts["read_failures"] == 1
        assert any("library.read en erreur" in warning for warning in plan.warnings)

        result = tool.run(plan, Control())

        assert result.ok is False
        assert result.facts["invalid"] == 0
        assert result.facts["read_failures"] == 1


def _make_integrity_tool(root, entries, artifacts=None):
    metadata = FakeMetadataBroker(entries)
    library_read = FakeLibraryReadBroker(artifacts or {})
    context = PluginContext(
        plugin_id="maintenance.audit.integrity",
        package_id="org.cifereca.maintenance.library-audit",
        directories=PluginDirectories(data=root / "data", cache=root / "cache"),
        _broker_resolver=lambda name: {
            "metadata": metadata, "library.read": library_read,
        }.get(name),
    )
    tool = IntegrityAuditTool().activate(context)
    return tool, tool.plan(MaintenanceContext(scope="library", identity_id="id1"))


def test_empty_integrity_run_never_invents_one_of_one_progress():
    with tempfile.TemporaryDirectory() as tmp:
        tool, plan = _make_integrity_tool(Path(tmp), [])
        assert plan.facts["candidates"] == ()
        control = Control()
        outcome = tool.run(plan, control)
        assert outcome.ok is True
        assert outcome.changed == 0
        assert outcome.facts["checked"] == 0
        assert outcome.facts["invalid"] == 0
        assert len(control.events) == 2
        assert all("current" not in event and "total" not in event
                   for event in control.events)
        assert "aucun fichier" in control.events[-1]["message"].lower()


def test_integrity_progress_counts_only_processed_files_without_private_paths():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        epub = root / "PRIVATE_FIRST.epub"
        pdf = root / "PRIVATE_SECOND.pdf"
        write_valid_epub(epub)
        pdf.write_bytes(b"%PDF-1.4\nPRIVATE_TEST")
        rows = [
            {"identity_id": "id1", "local": True,
             "local_path": str(epub), "title": "PRIVATE TITLE ONE"},
            {"identity_id": "id1", "local": True,
             "local_path": str(pdf), "title": "PRIVATE TITLE TWO"},
        ]
        tool, plan = _make_integrity_tool(root, rows)
        control = Control()
        result = tool.run(plan, control)
        assert result.ok is True
        assert result.facts["checked"] == 2
        assert [(event.get("current"), event.get("total"))
                for event in control.events] == [(0, 2), (1, 2), (2, 2)]
        assert control.events[-1]["message"] == "Audit d’intégrité terminé"
        assert all("PRIVATE" not in event["message"] and str(root) not in event["message"]
                   for event in control.events)


def test_cancellation_on_last_integrity_file_prevents_completed_progress():
    class Aborted(Exception):
        pass

    class CancellingControl(Control):
        def __init__(self):
            super().__init__()
            self.checked = 0

        def check_cancelled(self):
            self.checked += 1
            if self.checked == 3:  # At the final gate, after the file scan.
                raise Aborted()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        epub = root / "PRIVATE.epub"
        write_valid_epub(epub)
        tool, plan = _make_integrity_tool(root, [
            {"identity_id": "id1", "local": True, "local_path": str(epub)}
        ])
        control = CancellingControl()
        with pytest.raises(Aborted):
            tool.run(plan, control)
        assert control.events == [{
            "current": 0, "total": 1, "message": "Intégrité 1/1",
        }]


def test_integrity_symlink_is_reported_without_opening_its_target(monkeypatch, tmp_path):
    external = tmp_path / "PRIVATE_EXTERNAL.pdf"
    external.write_bytes(b"%PDF-1.4\\nPRIVATE_PAYLOAD")
    linked = tmp_path / "linked.pdf"
    linked.symlink_to(external)
    dangling = tmp_path / "dangling.epub"
    dangling.symlink_to(tmp_path / "PRIVATE_NONEXISTENT.epub")

    def unexpected_open(*_args, **_kwargs):
        raise AssertionError("Symlink audit may not open its target")

    with monkeypatch.context() as patch:
        patch.setattr(audits.os, "open", unexpected_open)
        for path in (linked, dangling):
            assert audits._file_integrity_issues(
                path, expected_size=999, expected_sha256="0" * 64
            ) == ("symlink",)

    tool, plan = _make_integrity_tool(tmp_path, [
        {"identity_id": "id1", "local": True,
         "local_path": str(linked), "title": "PRIVATE LINK"},
        {"identity_id": "id1", "local": True,
         "local_path": str(dangling), "title": "PRIVATE DANGLING"},
    ])
    control = Control()
    result = tool.run(plan, control)
    assert result.changed == 0
    assert result.ok is False
    assert result.facts["checked"] == 2
    assert result.facts["invalid"] == 2
    assert result.facts["missing"] == 0
    assert result.facts["issues"] == {"symlink": 2}
    assert [(event.get("current"), event.get("total"))
            for event in control.events] == [(0, 2), (1, 2), (2, 2)]
    assert all("PRIVATE" not in event["message"] for event in control.events)
    assert external.read_bytes() == b"%PDF-1.4\\nPRIVATE_PAYLOAD"


def test_integrity_rejects_symlink_swapped_between_check_and_open(monkeypatch, tmp_path):
    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("Atomic no-follow open is unavailable on this platform")
    external = tmp_path / "PRIVATE_TARGET.epub"
    write_valid_epub(external)
    subject = tmp_path / "switch.epub"
    write_valid_epub(subject)
    original_open = os.open
    flags_seen = []

    def substituted_open(path, flags, *args, **kwargs):
        if Path(path) == subject:
            flags_seen.append(flags)
            subject.unlink()
            subject.symlink_to(external)
        return original_open(path, flags, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(audits.os, "open", substituted_open)
        issues = audits._file_integrity_issues(
            subject, expected_size=999, expected_sha256="0" * 64,
        )

    assert issues == ("symlink",)
    assert len(flags_seen) == 1
    assert flags_seen[0] & os.O_NOFOLLOW
    assert external.is_file()


def test_integrity_fails_closed_when_nofollow_not_supported(monkeypatch, tmp_path):
    ordinary = tmp_path / "local.pdf"
    ordinary.write_bytes(b"%PDF-1.4\\nno redirect")
    seen = []

    def no_open(*_args, **_kwargs):
        seen.append(True)
        raise AssertionError("Cannot open without atomic no-follow")

    with monkeypatch.context() as patch:
        patch.delattr(audits.os, "O_NOFOLLOW", raising=False)
        patch.setattr(audits.os, "open", no_open)
        assert audits._file_integrity_issues(ordinary, "pdf") == ("unreadable",)
    assert seen == []
    assert ordinary.read_bytes().startswith(b"%PDF-")
