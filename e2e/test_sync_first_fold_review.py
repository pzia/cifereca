"""Synthetic data only: the reviewer never discloses source screen.json text."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sync_first_fold_review import compare, main, review


SCOPE = "real MainWindow and real Diagnostics page contribution, simulated brokers"


def sample():
    return {
        "source_cards": 11, "source_status_rows": 11, "detail_rows": 23,
        "source_summary_lines": 88, "action_buttons": 40,
        "primary_source_actions": 11, "visible_source_status_rows": 2,
        "visible_primary_source_actions": 2, "visible_action_buttons": 3,
        "window_width": 1280, "window_height": 850,
        "viewport_width": 1100, "viewport_height": 680,
        "scroll_content_height": 8000.0, "scroll_extent_viewports": 10.76,
        "global_action_visible": True, "secret_url": "https://secret.example/token=123",
    }


class FirstFoldReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "screen.json"

    def write(self, **overrides):
        data = {
            "status": "PASS", "scope": SCOPE,
            "sync_first_fold_ux": sample(),
            "sync_1280_first_fold_ux": {
                "normal_font": sample(), "large_font": sample(),
            },
            "private": "PRIVATE_SYNC_ERROR password=secret",
        }
        data.update(overrides)
        self.file.write_text(json.dumps(data))

    def test_aggregates_allowlisted_and_ratios(self):
        self.write()
        summary = review(self.file)
        self.assertEqual(summary["samples"]["baseline"]["sources_visible_percent"], 18.2)
        self.assertTrue(summary["samples"]["1280_large_font"]["target_width_reached"])
        self.assertNotIn("secret", json.dumps(summary).lower())
        self.assertNotIn("PRIVATE_SYNC_ERROR", json.dumps(summary))

    def test_bad_shape_never_leaks_exception_or_paths(self):
        bad = sample()
        bad["visible_action_buttons"] = 400
        self.write(sync_first_fold_ux=bad)
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main([str(self.file)])
        self.assertEqual(rc, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertNotIn(str(self.file), err.getvalue())
        self.assertNotIn("token", err.getvalue())

    def test_reject_unverified_and_bool_as_count(self):
        self.write(status="FAIL")
        with self.assertRaises(ValueError):
            review(self.file)
        bad = sample()
        bad["source_cards"] = True
        self.write(sync_first_fold_ux=bad)
        with self.assertRaises(ValueError):
            review(self.file)

    def test_input_symlink_refused_and_no_1280_supported(self):
        self.write(sync_1280_first_fold_ux=None)
        self.assertEqual(list(review(self.file)["samples"]), ["baseline"])
        link = Path(self.tmp.name) / "link.json"
        link.symlink_to(self.file)
        with self.assertRaises(ValueError):
            review(link)

    def test_actual_window_width_is_not_assumed(self):
        other = sample()
        other["window_width"] = 1366
        self.write(sync_1280_first_fold_ux={"normal_font": other})
        self.assertFalse(review(self.file)["samples"]["1280_normal_font"]["target_width_reached"])


    def test_compare_matched_source_fixture_reports_bounded_deltas(self):
        self.write()
        before = review(self.file)
        improved = sample()
        improved["visible_source_status_rows"] = 4
        improved["visible_primary_source_actions"] = 3
        improved["scroll_extent_viewports"] = 8.76
        self.write(sync_first_fold_ux=improved)
        delta = compare(before, review(self.file))
        sample_delta = delta["samples"]["baseline"]
        self.assertTrue(sample_delta["comparable"])
        self.assertEqual(sample_delta["source_visibility_points_delta"], 18.2)
        self.assertEqual(sample_delta["primary_action_visibility_points_delta"], 9.1)
        self.assertEqual(sample_delta["scroll_viewports_delta"], -2.0)
        self.assertNotIn("secret", json.dumps(delta).lower())

    def test_compare_refuses_unmatched_window_and_population(self):
        self.write()
        before = review(self.file)
        wider = sample()
        wider["window_width"] = 1400
        self.write(sync_first_fold_ux=wider)
        mismatch = compare(before, review(self.file))["samples"]["baseline"]
        self.assertFalse(mismatch["comparable"])
        self.assertEqual(mismatch["reason"], "different_window_geometry")
        altered = sample()
        altered["source_cards"] = 12
        self.write(sync_first_fold_ux=altered)
        mismatch = compare(before, review(self.file))["samples"]["baseline"]
        self.assertFalse(mismatch["comparable"])
        self.assertEqual(mismatch["reason"], "different_source_population")

    def test_cli_before_after_does_not_echo_private_file_or_payload(self):
        self.write()
        earlier = Path(self.tmp.name) / "private-before.json"
        earlier.write_bytes(self.file.read_bytes())
        after_sample = sample()
        after_sample["visible_source_status_rows"] = 3
        self.write(sync_first_fold_ux=after_sample)
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([str(self.file), "--before", str(earlier)])
        self.assertEqual(code, 0)
        self.assertEqual(err.getvalue(), "")
        self.assertTrue(json.loads(out.getvalue())["comparison"]["samples"]["baseline"]["comparable"])
        self.assertNotIn(str(self.file), out.getvalue())
        self.assertNotIn(str(earlier), out.getvalue())
        self.assertNotIn("PRIVATE_SYNC_ERROR", out.getvalue())
        self.assertNotIn("secret", out.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
