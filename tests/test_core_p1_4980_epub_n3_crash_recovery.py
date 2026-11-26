"""#4980: process interruption before atomic EPUB N3 publication."""
import os
import subprocess
import sys

from cifereca import epub_metadata
from cifereca.core.epub_cover_materialization import EpubCoverPhysicalCache
from tests.test_core_p1_6168_epub_cover_materialization import prepared


def test_interrupted_publication_recovers_exact_image(prepared):
    book, ref, image = prepared
    expected = image.read_bytes()
    image.unlink()
    script = """
import os
import sys
from pathlib import Path
from cifereca import epub_metadata

def interrupted(source, target):
    assert Path(source).read_bytes() == data
    assert not Path(target).exists()
    os._exit(73)

data = bytes.fromhex(sys.argv[1])
epub_metadata.os.replace = interrupted
epub_metadata._publish_cover(data, sys.argv[2])
"""
    child = subprocess.run(
        [sys.executable, "-c", script, expected.hex(), image.suffix[1:]],
        env=os.environ.copy(), capture_output=True, timeout=20,
    )
    assert child.returncode == 73, child.stderr.decode(errors="replace")
    assert not image.exists()
    leftovers = list(image.parent.glob(".cover-*"))
    assert len(leftovers) == 1
    assert not epub_metadata.valid_cached_cover_path(leftovers[0])
    cache = EpubCoverPhysicalCache()
    try:
        assert cache.request(ref, source_path=book).result(timeout=10) == image.resolve().as_uri()
        assert image.read_bytes() == expected
        assert epub_metadata.valid_cached_cover_path(image)
        assert not epub_metadata.valid_cached_cover_path(leftovers[0])
    finally:
        cache.close()
