"""#4093: integrity checks include HTTP-to-body/HTML links, not just ZIP SHA.

No real provider, private traffic, I/O outside synthetic temp archives.
"""
from __future__ import annotations

from hashlib import sha256
import json
import zipfile

import pytest

from cifereca.core.capture_session_export import (
    export_capture_session, validate_capture_session,
)
from tests.test_core_p1_4093_capture_session_export import (
    _entry, _http, _journal,
)


def _sample(tmp_path):
    raw = _entry("a" * 32, b"<html>PRIVATE_SYNTHETIC</html>",
                 [["content-type", "text/html; charset=utf-8"]])
    return export_capture_session(
        tmp_path, journal=_journal(), http_snapshot=_http([raw]),
        app_exit_code=0,
    )


def _rewrite(source, target, transform):
    with zipfile.ZipFile(source) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    index = json.loads(members["http/index.json"])
    manifest = json.loads(members["manifest.json"])
    transform(index, manifest, members)
    members["http/index.json"] = json.dumps(index, ensure_ascii=True).encode()
    # The attack/altered example is *self-consistently re-signed* with new SHA.
    # A hash-only reader would accept all altered files as "valid".
    manifest["members"] = {
        name: {"sha256": sha256(value).hexdigest(), "bytes": len(value)}
        for name, value in members.items() if name != "manifest.json"
    }
    members["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(target, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in members.items():
            archive.writestr(name, value)


def test_legitimate_capture_index_matches_every_blob_and_html(tmp_path):
    source = _sample(tmp_path)
    assert validate_capture_session(source)["status"] == "valid"
    with zipfile.ZipFile(source) as archive:
        index = json.loads(archive.read("http/index.json"))
        assert index["html_projections"] == [{
            "exchange_ref": "a" * 32, "member": "html/" + "a" * 32 + ".html",
        }]


@pytest.mark.parametrize("kind", [
    "orphan_body", "orphan_html", "relabel_body", "unlisted_html",
    "wrong_count", "duplicate_ref", "missing_blob",
])
def test_consistent_archive_hashes_cannot_hide_invalid_http_links(tmp_path, kind):
    source = _sample(tmp_path)
    changed = tmp_path / f"{kind}.zip"

    def mutate(index, manifest, members):
        row = index["exchanges"][0]
        ref = "a" * 32
        blob = f"http/bodies/{ref}-response.bin"
        html_name = f"html/{ref}.html"
        if kind == "orphan_body":
            members[f"http/bodies/{'b' * 32}-request.bin"] = b"PRIVATE_EXTRA"
        elif kind == "orphan_html":
            members[f"html/{'b' * 32}.html"] = b"PRIVATE_EXTRA"
        elif kind == "relabel_body":
            row["response_body"]["sha256"] = "b" * 64
        elif kind == "unlisted_html":
            index["html_projections"] = []
            manifest["html_projected"] = 0
        elif kind == "wrong_count":
            manifest["http_exchanges"] = 99
        elif kind == "duplicate_ref":
            index["exchanges"].append(row.copy())
            manifest["http_exchanges"] = 2
        elif kind == "missing_blob":
            del members[blob]

    _rewrite(source, changed, mutate)
    with pytest.raises(ValueError, match="capture_archive_invalid"):
        validate_capture_session(changed)


def test_export_refuses_duplicate_exchange_ids_before_writing(tmp_path):
    record = _entry("f" * 32, b"PRIVATE", [
        ["content-type", "text/plain"],
    ])
    with pytest.raises(ValueError, match="capture_http_snapshot_invalid"):
        export_capture_session(
            tmp_path, journal=_journal(), http_snapshot=_http([record, record]),
            app_exit_code=0,
        )
    assert not list(tmp_path.glob("cifereca-capture-*.zip"))
