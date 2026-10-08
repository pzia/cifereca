from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import zipfile

from cifereca.plugin_api import (
    ArtifactFormat,
    ArtifactRef,
    LibraryReadBroker,
    MaintenanceContext,
    MetadataBroker,
    PluginContext,
    PluginDirectories,
)

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
