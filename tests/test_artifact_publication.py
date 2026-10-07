from __future__ import annotations

from pathlib import Path

from cifereca.plugin_api import publish_artifact_content


def test_publish_artifact_content_creates_parent_and_replaces_atomically(tmp_path: Path):
    destination = tmp_path / "nested" / "artifact.bin"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"old")

    result = publish_artifact_content(destination, b"new")

    assert result == destination
    assert destination.read_bytes() == b"new"
    assert not list(destination.parent.glob(f".{destination.name}.*.part"))
