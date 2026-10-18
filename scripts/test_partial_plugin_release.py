from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.package_partial_plugins import (
    PARTIAL_MANIFEST,
    build_partial_snapshot,
    verify_partial_snapshot,
)
from scripts.plugin_packaging import PackagingError, validate_dev_snapshot
from scripts.ci_plugins_release_target import ReleaseTargetRefused, validate as validate_release_target
from scripts.test_plugin_packaging import make_plugin


class PartialPluginReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plugins = self.root / "plugins"
        self.plugins.mkdir()
        make_plugin(self.plugins, "alpha", "org.cifereca.provider.alpha", "1.2.3")
        make_plugin(self.plugins, "beta", "org.cifereca.provider.beta", "2.0")

    def build(self, *packages: str) -> Path:
        return build_partial_snapshot(
            packages,
            plugins_dir=self.plugins,
            output_dir=self.root / "dist",
            snapshot_id="plugins-preview-test",
        )

    def test_one_package_contains_no_core_and_embedded_metadata(self) -> None:
        archive = self.build("org.cifereca.provider.alpha")
        dev = validate_dev_snapshot(archive)
        self.assertNotIn("core", dev)
        self.assertEqual(len(dev["plugins"]), 1)
        metadata = verify_partial_snapshot(archive)
        self.assertEqual(metadata["kind"], "cifereca-plugins-only-preview")
        self.assertEqual(metadata["id"], dev["id"])
        self.assertTrue(metadata["core_version_tested"])
        self.assertEqual(
            [row["package_id"] for row in metadata["packages"]],
            ["org.cifereca.provider.alpha"],
        )
        record = metadata["packages"][0]
        self.assertEqual(record["version"], "1.2.3")
        self.assertEqual(record["plugin_api"], ">=1.3,<2")
        self.assertEqual(record["plugins"], [{"id": "alpha.service", "version": "1.2.3"}])
        self.assertEqual(record["sha256"], dev["plugins"][0]["sha256"])
        with zipfile.ZipFile(archive) as z:
            self.assertIn("plugins-preview-test/" + PARTIAL_MANIFEST, z.namelist())
            self.assertEqual(
                hashlib.sha256(z.read("plugins-preview-test/" + record["path"])).hexdigest(),
                record["sha256"],
            )
        expected_checksum = f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
        self.assertEqual(
            archive.with_suffix(archive.suffix + ".sha256").read_text(encoding="utf-8"),
            expected_checksum,
        )

    def test_multiple_packages_have_sorted_complete_index(self) -> None:
        archive = self.build("org.cifereca.provider.beta", "org.cifereca.provider.alpha")
        metadata = verify_partial_snapshot(archive)
        self.assertEqual(
            [entry["package_id"] for entry in metadata["packages"]],
            ["org.cifereca.provider.alpha", "org.cifereca.provider.beta"],
        )
        self.assertEqual(len(validate_dev_snapshot(archive)["plugins"]), 2)

    def test_unknown_duplicate_and_empty_selection_rejected(self) -> None:
        for selected, expected in [
            ((), "au moins un"),
            (("org.cifereca.provider.alpha", "org.cifereca.provider.alpha"), "dupliqué"),
            (("org.cifereca.provider.unknown",), "inconnus"),
        ]:
            with self.subTest(selected=selected):
                with self.assertRaisesRegex(PackagingError, expected):
                    self.build(*selected)

    def test_target_core_and_api_preflight_accepts_exact_tested_environment(self) -> None:
        archive = self.build("org.cifereca.provider.alpha", "org.cifereca.provider.beta")
        metadata = verify_partial_snapshot(archive)
        accepted = verify_partial_snapshot(
            archive,
            target_core_version=metadata["core_version_tested"],
            target_plugin_api_version="1.4.0",
        )
        self.assertEqual(accepted, metadata)

    def test_target_core_drift_is_rejected_even_when_api_is_compatible(self) -> None:
        archive = self.build("org.cifereca.provider.alpha")
        with self.assertRaisesRegex(PackagingError, "Core cible"):
            verify_partial_snapshot(
                archive, target_core_version="0.0.0",
                target_plugin_api_version="1.4.0",
            )

    def test_target_api_incompatibility_is_rejected_before_install(self) -> None:
        archive = self.build("org.cifereca.provider.alpha")
        tested = verify_partial_snapshot(archive)["core_version_tested"]
        with self.assertRaisesRegex(PackagingError, "incompatible avec Plugin API"):
            verify_partial_snapshot(
                archive, target_core_version=tested,
                target_plugin_api_version="3.0.0",
            )
        with self.assertRaisesRegex(PackagingError, "Plugin API cible invalide"):
            verify_partial_snapshot(
                archive, target_core_version=tested,
                target_plugin_api_version="not-a-version",
            )

    def test_partial_target_specification_is_forbidden(self) -> None:
        archive = self.build("org.cifereca.provider.alpha")
        for kwargs in (
            {"target_core_version": "0.9.44+dev.11"},
            {"target_plugin_api_version": "1.4.0"},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(PackagingError, "requises ensemble"):
                    verify_partial_snapshot(archive, **kwargs)

    def test_official_release_verifies_signed_source_before_publishing(self) -> None:
        """Regression #787: sidecar-only publication must not be possible."""
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/plugins-only-release.yml"
        ).read_text(encoding="utf-8")
        release_job = workflow.split("\n  release:\n", 1)[1]
        steps = (
            "name: Sign plugins-only ZIP with Sigstore keyless",
            "name: Verify signed provenance before publishing",
            "name: Create or update draft GitHub Release",
            "name: Publish completed plugins-only release",
        )
        positions = [release_job.index(step) for step in steps]
        self.assertEqual(positions, sorted(positions))
        verification = release_job[positions[1]:positions[2]]
        for evidence in (
            'sha256sum -c "$RELEASE_TAG.zip.sha256"',
            'cosign verify-blob "$archive"',
            '--bundle "$archive.sigstore.json"',
            '--certificate-identity "https://github.com/$GITHUB_REPOSITORY/.github/workflows/plugins-only-release.yml@refs/heads/main"',
            '--certificate-oidc-issuer "https://token.actions.githubusercontent.com"',
            '--certificate-github-workflow-repository "$GITHUB_REPOSITORY"',
            '--certificate-github-workflow-ref "refs/heads/main"',
            '--certificate-github-workflow-sha "$GITHUB_SHA"',
            "set -euo pipefail",
        ):
            self.assertIn(evidence, verification)
        self.assertNotIn("|| true", verification)

    def test_official_release_request_is_explicit_and_scoped(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/plugins-only-release.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('branches: [main]', workflow)
        self.assertIn('".github/plugins-only-release-request.json"', workflow)
        self.assertIn("name: Resolve explicitly authorized release request", workflow)
        self.assertIn("name: Verify downloaded draft artifact and repeat P0 gates", workflow)
        release_job = workflow.split("\n  release:\n", 1)[1]
        self.assertLess(
            release_job.index("name: Verify downloaded draft artifact and repeat P0 gates"),
            release_job.index("name: Publish completed plugins-only release"),
        )
        self.assertIn('gh release download "$RELEASE_TAG"', release_job)
        self.assertIn('--pattern "$RELEASE_TAG.zip.sigstore.json"', release_job)
        self.assertIn('cosign verify-blob "$artifact_dir/$RELEASE_TAG.zip"', release_job)
        self.assertNotIn("actions/attest@v4", release_job)
        self.assertIn('bash scripts/ci-plugins-only-runtime-gate.sh "$artifact_dir/$RELEASE_TAG.zip"', release_job)
        self.assertIn('bash scripts/ci-plugins-only-historical-core-gate.sh "$artifact_dir/$RELEASE_TAG.zip"', release_job)

    def test_official_publication_requires_an_existing_real_core_release(self) -> None:
        """P0 #787: same version in main is not proof of a distributed Core."""
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/plugins-only-release.yml").read_text(encoding="utf-8")
        runtime_gate = (root / "scripts/ci-plugins-only-runtime-gate.sh").read_text(encoding="utf-8")
        release_job = workflow.split("\n  release:\n", 1)[1]
        published = release_job.index("name: Require published Core and validate the actual distributed wheel")
        built = release_job.index("name: Build selected official plugins-only snapshot")
        exercised = release_job.index("name: Exercise real strict install, incompatible refusal and rollback")
        signed = release_job.index("name: Sign plugins-only ZIP with Sigstore keyless")
        drafted = release_job.index("name: Create or update draft GitHub Release")
        self.assertLess(built, published)
        self.assertLess(published, exercised)
        self.assertLess(exercised, signed)
        self.assertLess(signed, drafted)
        gate = release_job[published:exercised]
        for required in (
            'gh api "repos/$GITHUB_REPOSITORY/releases/tags/$core_tag"',
            'gh release download "$core_tag"',
            '--pattern "$wheel" --pattern "SHA256SUMS"',
            'git archive "$core_tag"',
            'git merge-base --is-ancestor "$tag_sha" "$GITHUB_SHA"',
            'assets[0].get("digest") != "sha256:" + digest',
            'release.get("target_commitish") != commit',
            '"$core_dir/venv/bin/cifereca-update-dev" --help',
            "grep -q -- '--require-plugins-only'",
            "grep -q -- '--expected-sha256'",
            'echo "CIFERECA_P0_CORE_SOURCE=$core_dir/source" >> "$GITHUB_ENV"',
        ):
            self.assertIn(required, gate)
        self.assertIn('os.environ.get("CIFERECA_P0_CORE_SOURCE")', runtime_gate)
        self.assertNotIn("|| true", gate)

    def test_plugins_only_draft_is_bound_to_exact_source_commit(self) -> None:
        tag, sha = "plugins-v0.9.44+dev.12-002", "a" * 40
        exact = {"tag_name": tag, "draft": True, "target_commitish": sha}
        validate_release_target(exact, tag, sha)
        for invalid in (
            dict(exact, target_commitish="b" * 40),
            dict(exact, tag_name="plugins-v0.9.44+dev.12-001"),
            dict(exact, draft=False),
            dict(exact, draft="true"),
            {},
            None,
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ReleaseTargetRefused):
                    validate_release_target(invalid, tag, sha)
        for bad_tag, bad_sha in ((tag + ";exit", sha), (tag, "main")):
            with self.assertRaises(ReleaseTargetRefused):
                validate_release_target(exact, bad_tag, bad_sha)

    def test_release_checks_source_before_asset_mutation_and_publication(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/plugins-only-release.yml"
        ).read_text(encoding="utf-8")
        release_job = workflow.split("\n  release:\n", 1)[1]
        validator = "python3 scripts/ci_plugins_release_target.py"
        self.assertEqual(release_job.count(validator), 3)
        self.assertLess(release_job.index(validator), release_job.index("gh release upload"))
        self.assertLess(
            release_job.index("name: Recheck immutable draft target before publication"),
            release_job.index("name: Publish completed plugins-only release"),
        )
        self.assertIn('"scripts/ci_plugins_release_target.py"', workflow)

    def test_modified_embedded_metadata_is_detected(self) -> None:
        original = self.build("org.cifereca.provider.alpha")
        corrupted = self.root / "tampered.zip"
        with zipfile.ZipFile(original) as input_zip, zipfile.ZipFile(corrupted, "w") as output_zip:
            for item in input_zip.infolist():
                payload = input_zip.read(item.filename)
                if item.filename.endswith("/" + PARTIAL_MANIFEST):
                    obj = json.loads(payload)
                    obj["packages"][0]["version"] = "99.0"
                    payload = json.dumps(obj).encode("utf-8")
                output_zip.writestr(item, payload)
        with self.assertRaisesRegex(PackagingError, "incohérent"):
            verify_partial_snapshot(corrupted)


if __name__ == "__main__":
    unittest.main()
