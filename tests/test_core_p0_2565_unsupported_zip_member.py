"""#2565: non-standard ZIP member compression must not abort a whole audit."""
from __future__ import annotations

import io
import json
import struct
import zipfile

import pytest

from cifereca.core.library_inventory import scan_identity_roots
from cifereca.core.library_structure import inspect_stream_structure
from tests.test_library_reconciliation import epub


def _unsupported_chapter(path):
    """Change one ordinary fixture ZIP member to an unsupported method.

    The local and central ZIP compression method fields must agree so that
    Python's own ZipExtFile raises NotImplementedError on opening the member.
    The EPUB container and OPF remain parseable and keep metadata inspection
    from masking the structural CRC pass being tested.
    """
    data = bytearray(path.read_bytes())
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        local = archive.getinfo("cover.png").header_offset
    assert data[local:local + 4] == bytes.fromhex("504b0304")
    method = struct.pack("<H", 99)
    data[local + 8:local + 10] = method
    central = 0
    found = False
    while (central := data.find(bytes.fromhex("504b0102"), central)) >= 0:
        length = struct.unpack_from("<H", data, central + 28)[0]
        name = bytes(data[central + 46:central + 46 + length])
        if name == b"cover.png":
            data[central + 10:central + 12] = method
            found = True
            break
        central += 4
    assert found
    path.write_bytes(data)
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(NotImplementedError):
            archive.open("cover.png")


def test_unsupported_member_rejected_with_closed_status_not_exception(tmp_path):
    root = tmp_path / "PRIVATE_SYNTHETIC_BOOKS"
    root.mkdir()
    damaged = root / "PRIVATE_UNSUPPORTED.epub"
    healthy = root / "PRIVATE_HEALTHY.epub"
    epub(damaged, title="PRIVATE_UNSUPPORTED_TITLE")
    epub(healthy, title="PRIVATE_HEALTHY_TITLE")
    _unsupported_chapter(damaged)

    with damaged.open("rb") as stream:
        before = stream.read()
        assert inspect_stream_structure(stream, "epub") == "structure_invalid"
        assert not stream.closed
    assert damaged.read_bytes() == before

    inventory = scan_identity_roots({"alice": (root,)})
    assert len(inventory.files) == 2
    by_path = {row.path: row for row in inventory.files}
    assert by_path[str(damaged)].validation == "ok"
    assert by_path[str(damaged)].structure_validation == "structure_invalid"
    assert by_path[str(healthy)].structure_validation == "epub_structure_crc_ok"
    assert "structure_limit_or_invalid" in inventory.reasons
    report = json.dumps(inventory.redacted())
    assert "PRIVATE_" not in report
    assert str(root) not in report
    assert by_path[str(damaged)].sha256 not in report
