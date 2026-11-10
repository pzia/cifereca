"""Shared synthetic EPUB fixture retained for cross-provider crash tests.

Historical Kobo W2 execution tests were retired; this helper performs no
provider I/O and does not grant rights or publish canonical evidence.
"""
from pathlib import Path
import zipfile


def _write_epub(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("content.xhtml", "<html><body>Test</body></html>")
