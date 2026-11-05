"""#3252: Kobo candidate ZIP format must agree with Core EPUB detection.

No provider request, DRM, token or private account is involved.
"""
from __future__ import annotations

from zipfile import ZipFile

import pytest

from cifereca.artifact_inspection import detect_artifact_format
from cifereca_kobo_selected_candidate import _candidate_format_ok


@pytest.mark.parametrize(("container", "mimetype", "admitted"), [
    (True, b"application/epub+zip", True),
    (True, None, True),  # The existing Core detector permits absent mimetype.
    (False, b"application/epub+zip", False),
    (True, b"application/x-not-epub", False),
    (False, None, False),
])
def test_candidate_zip_preflight_matches_core_epub_detection(
    tmp_path, container, mimetype, admitted,
):
    path = tmp_path / "synthetic.epub"
    with ZipFile(path, "w") as archive:
        if mimetype is not None:
            archive.writestr("mimetype", mimetype)
        if container:
            archive.writestr(
                "META-INF/container.xml",
                '<?xml version="1.0"?><container/>',
            )
        archive.writestr("payload.dat", "synthetic")
    assert _candidate_format_ok(path, "epub") is admitted
    assert (detect_artifact_format(path) == "epub") is admitted


def test_non_zip_never_qualifies_as_epub(tmp_path):
    path = tmp_path / "fake.epub"
    path.write_bytes(b"PK not actually a ZIP archive")
    assert _candidate_format_ok(path, "epub") is False
    assert detect_artifact_format(path) is None
