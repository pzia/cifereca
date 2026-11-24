import json
import zipfile

import pytest

from cifereca.core.notice_regression_capture import write_regression_capture


def test_capture_partial_idempotent_and_private(tmp_path, monkeypatch):
    monkeypatch.setattr("cifereca.core.notice_regression_capture.downloads_dir",
                        lambda: tmp_path)
    args = dict(diagnostic_id="a" * 64, generation=1,
                members={"diff.json": b'{"missing":["title"]}'})
    path, status = write_regression_capture(**args)
    assert path.parent == tmp_path and status == "partial"
    assert path.stat().st_mode & 0o077 == 0
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert "response.raw" in manifest["omitted"]
        assert archive.read("diff.json") == args["members"]["diff.json"]
    assert write_regression_capture(**args) == (path, status)
    assert list(tmp_path.iterdir()) == [path]
    with pytest.raises(ValueError, match="conflict"):
        write_regression_capture(**{**args, "members": {"diff.json": b"other"}})


def test_capture_refuses_traversal_and_oversized_input(tmp_path):
    for members in ({"../secret": b"x"}, {"response.raw": b"x" * (8*1024*1024+1)}):
        with pytest.raises(ValueError):
            write_regression_capture(diagnostic_id="b"*64, generation=1,
                                     members=members, directory=tmp_path)
    assert not list(tmp_path.iterdir())
