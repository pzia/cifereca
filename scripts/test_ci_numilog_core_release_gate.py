"""#2095: published-Core ancestry is mandatory for a Numilog-only release."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from scripts.ci_numilog_core_release_gate import (
    NUMILOG_ID, REQUIRED_CORE_COMMITS, NumilogReleaseRefused,
    validate_numilog_release_core,
)

TARGET = "a" * 40
OTHER_ID = "org.cifereca.provider.gallica"


class NumilogCoreGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = self.root / "numilog.json"
        self.write_version("0.1.69")

    def write_version(self, version: str) -> None:
        self.manifest.write_text(
            json.dumps({"package": {"id": NUMILOG_ID, "version": version}}),
            encoding="utf-8",
        )

    def validate(self, selected=(NUMILOG_ID,), target=TARGET, runner=None):
        if runner is None:
            runner = lambda *a, **kw: SimpleNamespace(returncode=0)
        return validate_numilog_release_core(
            selected, target, manifest_path=self.manifest,
            checkout=self.root, runner=runner,
        )

    def test_non_numilog_plugins_unaffected_without_git_or_numilog_manifest(self):
        self.manifest.unlink()
        def unexpected(*args, **kwargs):
            raise AssertionError("git must not run")
        self.assertEqual(
            self.validate([OTHER_ID], runner=unexpected), "not-selected",
        )

    def test_exact_published_core_tag_must_contain_every_required_merge(self):
        seen = []
        def run(args, **kwargs):
            self.assertEqual(kwargs["cwd"], self.root)
            self.assertIs(kwargs["check"], False)
            self.assertIs(kwargs["capture_output"], True)
            seen.append(args)
            return SimpleNamespace(returncode=0)
        self.assertEqual(self.validate(runner=run), "verified-core-ancestry")
        self.assertEqual(
            seen,
            [["git", "merge-base", "--is-ancestor", sha, TARGET]
             for sha in REQUIRED_CORE_COMMITS],
        )
        self.assertEqual(len(REQUIRED_CORE_COMMITS), 3)

    def test_missing_one_fix_or_missing_git_history_fails_closed(self):
        for failure_code in (1, 128):
            for missing in REQUIRED_CORE_COMMITS:
                with self.subTest(code=failure_code, required=missing):
                    def run(args, **kwargs):
                        return SimpleNamespace(
                            returncode=failure_code if args[3] == missing else 0
                        )
                    with self.assertRaisesRegex(
                        NumilogReleaseRefused, "does not contain"
                    ):
                        self.validate(runner=run)
        with self.assertRaisesRegex(
            NumilogReleaseRefused, "ancestry unavailable"
        ):
            self.validate(runner=lambda *a, **kw: (_ for _ in ()).throw(OSError()))

    def test_git_result_without_a_valid_success_exit_code_is_rejected(self):
        """A missing git result must never be interpreted as ancestry success."""
        for result in (None, object(), SimpleNamespace(returncode=None)):
            with self.subTest(result=type(result).__name__):
                with self.assertRaisesRegex(
                    NumilogReleaseRefused, "does not contain"
                ):
                    self.validate(runner=lambda *_a, **_k: result)

    def test_old_numilog_version_cannot_proceed_even_with_new_core(self):
        self.write_version("0.1.50")
        def unexpected(*args, **kwargs):
            raise AssertionError("git must not run")
        with self.assertRaisesRegex(NumilogReleaseRefused, "minimum version"):
            self.validate(runner=unexpected)
        self.write_version("0.1.51")
        self.assertEqual(self.validate(), "verified-core-ancestry")

    def test_invalid_manifest_and_bad_sha_do_not_fall_back_to_main(self):
        with self.assertRaisesRegex(NumilogReleaseRefused, "tag commit"):
            self.validate(target="v0.9.44+dev.12")
        for invalid in ("", "BAD", "0.1.69\nBearer PRIVATE", "x" * 100):
            with self.subTest(invalid=invalid):
                self.write_version(invalid)
                with self.assertRaises(NumilogReleaseRefused):
                    self.validate()
        self.manifest.write_text('{"package":{"id":"other","version":"0.1.69"}}')
        with self.assertRaisesRegex(NumilogReleaseRefused, "manifest"):
            self.validate()

    def test_selected_numilog_is_not_skipped_when_other_packages_selected(self):
        self.assertEqual(
            self.validate([OTHER_ID, NUMILOG_ID]), "verified-core-ancestry",
        )


if __name__ == "__main__":
    unittest.main()
