"""Offline security and compatibility tests for the Debian GTK cache fetcher."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from fetch_release_cache import main, _unpack_checked, PROFILE, ASSET, RELEASE_SHA256


def archive(path: Path, *, evil: str | None = None, bad_package: bool = False) -> str:
    with tarfile.open(path, "w:gz") as tar:
        def put(name: str, data: bytes, kind=tarfile.REGTYPE):
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(data) if kind == tarfile.REGTYPE else 0
            tar.addfile(member, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
        put(".gtk-deb-cache/.gtk4-cache-profile.json", json.dumps(PROFILE).encode())
        for i in range(12):
            put(f".gtk-deb-cache/test{i}_1.0_amd64.deb",
                b"wronghdr" if bad_package and i == 0 else b"!<arch>\n" + b"12345678")
        if evil is not None:
            put(evil, b"!<arch>\n0123456789")
    return hashlib.sha256(path.read_bytes()).hexdigest()


class GtkAssetTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.asset = self.root / "cache.tar.gz"

    def test_local_asset_extracts_and_checks_profile(self):
        digest = archive(self.asset)
        dest = self.root / "fresh-cache"
        self.assertEqual(main(["--archive", str(self.asset), "--sha256", digest,
                               "--dest", str(dest)]), 0)
        self.assertEqual(len(list(dest.glob("*.deb"))), 12)
        self.assertEqual(json.loads((dest / ".gtk4-cache-profile.json").read_text()), PROFILE)
        self.assertEqual(main(["--archive", str(self.asset), "--sha256", digest,
                               "--dest", str(dest)]), 2)

    def test_wrong_checksum_does_not_create_destination(self):
        archive(self.asset)
        dest = self.root / "missing"
        self.assertEqual(main(["--archive", str(self.asset), "--sha256", "0" * 64,
                               "--dest", str(dest)]), 2)
        self.assertFalse(dest.exists())

    def test_refuse_path_traversal(self):
        digest = archive(self.asset, evil=".gtk-deb-cache/../../escaped.deb")
        dest = self.root / "missing"
        self.assertEqual(main(["--archive", str(self.asset), "--sha256", digest,
                               "--dest", str(dest)]), 2)
        self.assertFalse(dest.exists())
        self.assertFalse((self.root / "escaped.deb").exists())

    def test_refuse_invalid_package_header(self):
        digest = archive(self.asset, bad_package=True)
        self.assertEqual(main(["--archive", str(self.asset), "--sha256", digest,
                               "--dest", str(self.root / "missing")]), 2)

    def test_refuse_symlink_tar_member(self):
        with tarfile.open(self.asset, "w:gz") as tar:
            entry = tarfile.TarInfo(".gtk-deb-cache/link_1.0_amd64.deb")
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/etc/passwd"
            tar.addfile(entry)
        with self.assertRaises(ValueError):
            _unpack_checked(self.asset, self.root)

    def test_remote_manifest_is_not_sufficient_without_pinned_digest(self):
        digest = archive(self.asset)
        self.assertNotEqual(digest, RELEASE_SHA256)
        dest = self.root / "missing"

        def fake_download(url, output, _maxbytes):
            if url.endswith("/SHA256SUMS"):
                output.write_text(f"{digest}  {ASSET}\n")
            else:
                output.write_bytes(self.asset.read_bytes())

        with patch("fetch_release_cache._fetch", side_effect=fake_download):
            self.assertEqual(main(["--dest", str(dest)]), 2)
        self.assertFalse(dest.exists())

    def test_digest_must_be_hex_and_filename_fixed(self):
        archive(self.asset)
        self.assertEqual(main(["--archive", str(self.asset), "--sha256", "bad",
                               "--dest", str(self.root / "missing")]), 2)


if __name__ == "__main__":
    unittest.main()
