from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from cifereca.acquisition import AcquisitionManager
from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import SearchResult


def _item(content_type="ebook", fmt="EPUB") -> SearchResult:
    return SearchResult(
        id="gallica:test", record_id="ark:/12148/test", docbase="GALLICA",
        medium="digital", title="Titre test", author=None, type="Livre numérique",
        format=fmt, publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url="https://gallica.bnf.fr/ark:/12148/test",
        scenario="TEST", source_provider="gallica", source_credential="public",
        download_url="https://example.test/book.epub" if content_type == "ebook" else "https://example.test/track.audio",
        content_type=content_type,
    )


def test_target_directory_priority_provider_content_then_provider_then_global_content_then_global():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = Config(root / "config.json")
        cfg.set("downloads.directory", str(root / "global"))
        cfg.set("downloads.content_type_directories.ebook", str(root / "global-ebook"))
        cfg.set_provider_settings("gallica", {"downloads": {
            "directory": str(root / "provider"),
            "content_type_directories": {"ebook": str(root / "provider-ebook"), "audiobook": ""},
        }})
        manager = AcquisitionManager(cfg, Diagnostics())
        assert manager.target_dir("gallica", "ebook") == root / "provider-ebook"
        assert manager.target_dir("gallica", "audiobook") == root / "provider"
        assert manager.target_dir("paris", "ebook") == root / "global-ebook"
        assert manager.target_dir("paris", "audiobook") == root / "global"


def test_download_override_receives_link_and_runs_post_download_hooks():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = Config(root / "config.json")
        target = root / "downloads"
        cfg.set("downloads.directory", str(target))
        cfg.set("downloads.command", [
            sys.executable, "-c",
            "from pathlib import Path; Path(r'{file}').write_text(r'{url}', encoding='utf-8')",
        ])
        cfg.set("downloads.post_download_command", [
            sys.executable, "-c",
            "from pathlib import Path; Path(r'{directory}/global-hook.ok').write_text(r'{content_type}')",
        ])
        cfg.set_provider_settings("gallica", {"downloads": {
            "post_download_command": [
                sys.executable, "-c",
                "from pathlib import Path; Path(r'{directory}/provider-hook.ok').write_text(r'{provider}')",
            ],
        }})
        manager = AcquisitionManager(cfg, Diagnostics(level="detailed"))
        item = _item()
        result = manager.run_override(item, manager.target_dir("gallica", "ebook"))
        assert result.action == "command"
        assert result.path and result.path.is_file()
        assert result.path.read_text(encoding="utf-8") == item.download_url
        assert (target / "global-hook.ok").read_text() == "ebook"
        assert (target / "provider-hook.ok").read_text() == "gallica"


def test_audio_external_command_uses_normalized_mp3_expected_extension_for_gallica_audio_url():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = Config(root / "config.json")
        manager = AcquisitionManager(cfg, Diagnostics())
        item = _item("audiobook", "MP3")
        path = manager.suggested_path(item, root, item.download_url or "")
        assert path.suffix == ".mp3"


def test_direct_acquisition_is_queue_ready_and_keeps_032_compatibility_aliases():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = Config(root / "config.json")
        manager = AcquisitionManager(cfg, Diagnostics())
        item = _item()
        artifact = root / "book.epub"
        artifact.write_bytes(b"PK\x03\x04")
        acquisition = manager.acquisition_from_direct(
            item, path=artifact, url=item.download_url or "", target_dir=root,
        )
        assert acquisition.provider_id == "gallica"
        assert acquisition.credential_id == "public"
        assert acquisition.account_id is None
        assert acquisition.kind == "direct_file"
        assert acquisition.drm == "none"
        assert acquisition.artifact_path == artifact
        assert acquisition.acsm_path is None
        assert acquisition.mime_type == "application/epub+zip"
        # Compatibility surface used by the 0.3.2 GUI.
        assert acquisition.path == artifact
        assert acquisition.url == item.download_url
        assert acquisition.action == "download"


def test_legacy_acquisition_result_constructor_remains_available():
    from cifereca.acquisition import AcquisitionResult

    result = AcquisitionResult(
        action="download", url="https://example.test/book.epub",
        target_dir=Path("/tmp"), path=Path("/tmp/book.epub"),
    )
    assert result.path == Path("/tmp/book.epub")
    assert result.url.endswith("book.epub")
