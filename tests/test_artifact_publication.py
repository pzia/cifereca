from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.plugin_api import publish_artifact_content, publish_artifact_file


def test_publish_artifact_content_creates_parent_and_replaces_atomically(tmp_path: Path):
    destination = tmp_path / "nested" / "artifact.bin"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"old")

    result = publish_artifact_content(destination, b"new")

    assert result == destination
    assert destination.read_bytes() == b"new"
    assert not list(destination.parent.glob(f".{destination.name}.*.part"))


def test_publish_artifact_file_accepts_identical_existing_destination(tmp_path: Path):
    source = tmp_path / "source.part"
    destination = tmp_path / "artifact.bin"
    source.write_bytes(b"same")
    destination.write_bytes(b"same")

    result = publish_artifact_file(source, destination, overwrite=False)

    assert result == destination
    assert source.read_bytes() == b"same"
    assert destination.read_bytes() == b"same"


def test_publish_artifact_file_refuses_different_existing_destination(tmp_path: Path):
    source = tmp_path / "source.part"
    destination = tmp_path / "artifact.bin"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")

    with pytest.raises(FileExistsError):
        publish_artifact_file(source, destination, overwrite=False)

    assert source.read_bytes() == b"new"
    assert destination.read_bytes() == b"old"


def test_publish_artifact_file_overwrite_moves_source(tmp_path: Path):
    source = tmp_path / "source.part"
    destination = tmp_path / "artifact.bin"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")

    result = publish_artifact_file(source, destination, overwrite=True)

    assert result == destination
    assert not source.exists()
    assert destination.read_bytes() == b"new"
