"""#577: post-install provenance must identify every selected package."""
from __future__ import annotations

from io import BytesIO
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.ci_plugins_only_receipt_gate import (
    InstalledReceiptRefused, RECEIPT_FILE, verify_installed_receipts,
)


class InstalledReceiptGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / "store"
        self.store.mkdir()
        self.snapshot_id = "plugins-v0.9.44+dev.12-009"
        self.source = "abcdef0123456789abcd"
        self.core = "0.9.44+dev.12"
        self.packages = (("org.cifereca.provider.alpha", "1.4.3"),
                         ("org.cifereca.provider.beta", "2.3.1"))
        self.archive = self.root / "snapshot.zip"
        self.records = []
        self.package_bytes = {}
        for name, version in self.packages:
            buffer = BytesIO()
            with zipfile.ZipFile(buffer, "w") as nested:
                nested.writestr("cifereca-plugin.json", json.dumps({
                    "package": {"id": name, "version": version}, "plugins": [],
                }))
            blob = buffer.getvalue()
            self.package_bytes[name] = blob
            self.records.append({
                "package_id": name, "version": version,
                "path": f"plugins/{name}.zip",
                "sha256": hashlib.sha256(blob).hexdigest(),
            })
        self._write_archive()
        self._write_receipts()

    def _write_archive(self, *, id=None, records=None):
        index = {
            "id": self.snapshot_id if id is None else id,
            "source_git_commit": self.source,
            "core_version_tested": self.core,
            "packages": self.records if records is None else records,
        }
        with zipfile.ZipFile(self.archive, "w") as z:
            z.writestr(self.snapshot_id + "/cifereca-plugins-only.json",
                       json.dumps(index))
            for row in self.records:
                z.writestr(self.snapshot_id + "/" + row["path"],
                           self.package_bytes[row["package_id"]])
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()

    def _receipt(self, package_id):
        return self.store / package_id / RECEIPT_FILE

    def _write_receipts(self):
        for row in self.records:
            path = self._receipt(row["package_id"])
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps({
                "schema": 1,
                "scope": "plugins-only",
                "package_id": row["package_id"],
                "version": row["version"],
                "snapshot_id": self.snapshot_id,
                "source_git_commit": self.source,
                "core_version_tested": self.core,
                "snapshot_sha256": self.digest,
                "package_sha256": row["sha256"],
                "trusted_digest_verified": True,
                "signature_verified": False,
            }))

    def validate(self):
        return verify_installed_receipts(
            self.archive, self.store, expected_sha256=self.digest,
        )

    def tamper(self, field, value):
        target = self._receipt(self.packages[0][0])
        data = json.loads(target.read_text())
        data[field] = value
        target.write_text(json.dumps(data))

    def test_two_package_release_all_receipts_are_exact_and_bounded(self):
        outcome = self.validate()
        self.assertEqual(outcome["selected_packages"], 2)
        self.assertEqual(outcome["snapshot_sha256"], self.digest)
        self.assertIs(outcome["signature_verified"], False)
        self.assertEqual(set(outcome), {
            "ok", "selected_packages", "snapshot_sha256", "signature_verified",
        })
        self.assertNotIn(str(self.root), json.dumps(outcome))

    def test_single_package_selection_works(self):
        self.records = self.records[:1]
        self._write_archive()
        self._write_receipts()
        self.assertEqual(self.validate()["selected_packages"], 1)

    def test_missing_second_receipt_rejects_partial_upgrade(self):
        self._receipt(self.packages[1][0]).unlink()
        with self.assertRaisesRegex(InstalledReceiptRefused, "Missing installed"):
            self.validate()

    def test_false_authenticity_and_bool_schema_spoofing_rejected(self):
        for field, value in (
            ("signature_verified", True),
            ("trusted_digest_verified", False),
            ("trusted_digest_verified", 1),
            ("schema", True),
            ("unreviewed_headers", {"Authorization": "PRIVATE"}),
        ):
            with self.subTest(field=field, value=value):
                self._write_receipts()
                self.tamper(field, value)
                with self.assertRaises(InstalledReceiptRefused):
                    self.validate()

    def test_snapshot_and_package_digest_or_identity_drift_refused(self):
        for key, value in (
            ("snapshot_sha256", "f" * 64),
            ("package_sha256", "f" * 64),
            ("snapshot_id", "wrong-tag"),
            ("source_git_commit", "a" * 40),
            ("core_version_tested", "9999.0"),
            ("version", "99.0"),
            ("package_id", "org.cifereca.provider.other"),
        ):
            with self.subTest(key=key):
                self._write_receipts()
                self.tamper(key, value)
                with self.assertRaises(InstalledReceiptRefused):
                    self.validate()

    def test_receipt_refuses_symlink_and_oversized_content(self):
        path = self._receipt(self.packages[0][0])
        other = self.root / "outside.json"
        other.write_text(path.read_text())
        path.unlink()
        path.symlink_to(other)
        with self.assertRaisesRegex(InstalledReceiptRefused, "symlink"):
            self.validate()
        path.unlink()
        path.write_text(" " * 3000)
        with self.assertRaisesRegex(InstalledReceiptRefused, "bounds"):
            self.validate()
        self.assertEqual(json.loads(other.read_text())["schema"], 1)

    def test_archive_or_index_tamper_cannot_validate_existing_receipt(self):
        original = self.digest
        self._write_archive(id="different-snapshot")
        with self.assertRaisesRegex(InstalledReceiptRefused, "index"):
            self.validate()
        self._write_archive()
        with zipfile.ZipFile(self.archive, "a") as archive:
            archive.writestr(self.snapshot_id + "/new-unreviewed-file", "x")
        self.assertNotEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(), original)
        with self.assertRaisesRegex(InstalledReceiptRefused, "archive digest"):
            self.validate()

    def test_modified_package_zip_sha_fails_even_with_consistent_receipt(self):
        self.records[0]["sha256"] = "f" * 64
        self._write_archive()
        self._write_receipts()
        with self.assertRaisesRegex(InstalledReceiptRefused, "package SHA-256"):
            self.validate()

    def test_unsafe_index_path_cannot_escape_archive_root(self):
        self.records[0]["path"] = "../private.zip"
        self._write_archive()
        self._write_receipts()
        with self.assertRaisesRegex(InstalledReceiptRefused, "Unsafe indexed archive"):
            self.validate()

    def test_never_echos_secret_containing_invalid_payload(self):
        self.tamper("provider_response", {"access_token": "PRIVATE_SECRET"})
        with self.assertRaises(InstalledReceiptRefused) as failure:
            self.validate()
        self.assertNotIn("PRIVATE_SECRET", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
