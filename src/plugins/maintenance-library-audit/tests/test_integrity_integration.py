from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import zipfile

from cifereca.artifact_runtime import LibraryReadBrokerImpl
from cifereca.brokers import MetadataBrokerImpl
from cifereca.plugin_api import MaintenanceContext, PluginContext, PluginDirectories

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


class Storage:
    def __init__(self, artifacts, placements):
        self._artifacts = {row["artifact_id"]: dict(row) for row in artifacts}
        self._placements = [dict(row) for row in placements]
        self.write_calls = []

    def artifact_get(self, artifact_id):
        row = self._artifacts.get(str(artifact_id))
        return dict(row) if row is not None else None

    def placements(self, state=None):
        rows = self._placements
        if state is not None:
            rows = [row for row in rows if row.get("state") == state]
        return [dict(row) for row in rows]

    def __getattr__(self, name):
        if name.startswith(("artifact_", "placement_", "metadata_", "book_")):
            def forbidden(*args, **kwargs):
                self.write_calls.append((name, args, kwargs))
                raise AssertionError(f"unexpected storage mutation: {name}")
            return forbidden
        raise AttributeError(name)


class Service:
    def __init__(self, storage, entries):
        self.storage = storage
        self._entries = [dict(row) for row in entries]

    def library_entries(self, **options):
        identity_id = options.get("identity_id")
        return [
            dict(row)
            for row in self._entries
            if identity_id is None or row.get("identity_id") == identity_id
        ]


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

        artifacts = []
        placements = []
        entries = []
        for artifact_id, path, media_format in zip(ids, paths, formats):
            exists = path.exists()
            artifacts.append({
                "artifact_id": artifact_id,
                "media_format": media_format,
                "mime_type": "application/epub+zip" if media_format == "epub" else "application/pdf",
                "size": path.stat().st_size if exists else 321,
                "sha256": sha256(path) if exists else "f" * 64,
                "metadata": {},
            })
            placements.append({
                "placement_id": "p-" + artifact_id,
                "artifact_id": artifact_id,
                "identity_id": "id1",
                "path": str(path),
                "state": "present",
                "metadata": {"placement_kind": "library"},
            })
            entries.append({
                "artifact_id": artifact_id,
                "identity_id": "id1",
                "title": artifact_id,
                "local": True,
                "local_path": str(path),
                "media_format": media_format,
            })

        # Force independent size/hash findings on the malformed PDF.
        artifacts[2]["size"] += 5
        artifacts[2]["sha256"] = "0" * 64

        storage = Storage(artifacts, placements)
        service = Service(storage, entries)
        metadata = MetadataBrokerImpl(service)
        library_read = LibraryReadBrokerImpl(service)

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

        # The audit must remain observational even when inconsistencies exist.
        assert storage.write_calls == []
