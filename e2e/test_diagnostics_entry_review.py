"""Synthetic-only tests; no private E2E data or GTK required."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from diagnostics_entry_review import CONTROL_IDS, compare, main, review


def control(*, present=True, visible=False, clipped=False):
    if not present:
        return {"present": False}
    return {
        "present": True, "allocated": True, "intersects_first_screen": visible,
        "fully_in_first_screen": visible, "horizontal_clipping": clipped,
        "vertical_visible_percent": 100.0 if visible else 0.0,
        "top_offset_viewports": 0.25 if visible else 2.6,
        "url": "https://private.example?token=secret",
    }


def sample(*, width=1280, visible=False):
    targets = {name: control(visible=visible) for name in CONTROL_IDS}
    return {
        "window_width": width, "window_height": 850, "viewport_width": width-80,
        "viewport_height": 750, "vertical_scroll_extent_viewports": 3.2,
        "controls": targets, "private": "USER_PASSWORD=secret",
    }


class DiagnosticsReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "screen.json"

    def write(self, *, status="PASS", entry=None, **other):
        payload = {
            "status": status, "plugin": "ui.page.diagnostics",
            "backend": "GTK4/X11", "broker": "fixture bornée",
            "diagnostics_entry_ux": entry if entry is not None else {
                "baseline": sample(width=1326), "requested_1280": sample(),
            }, "private": "PRIVATE_SYNC_ERROR password=secret",
        }
        payload.update(other)
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def test_allowlist_and_computed_viewport_summary(self):
        self.write()
        result = review(self.path)
        self.assertFalse(result["samples"]["requested_1280"]["journals_on_first_screen"])
        self.assertTrue(result["samples"]["requested_1280"]["target_width_reached"])
        self.assertNotIn("PRIVATE_SYNC_ERROR", json.dumps(result))
        self.assertNotIn("private.example", json.dumps(result))
        self.assertNotIn("USER_PASSWORD", json.dumps(result))

    def test_compare_journals_become_visible(self):
        self.write()
        earlier = review(self.path)
        after = sample()
        for name in ("journals_heading", "source_filter", "severity_filter", "period_filter"):
            after["controls"][name] = control(visible=True)
        self.write(entry={"baseline": sample(width=1326), "requested_1280": after})
        result = compare(earlier, review(self.path))["samples"]["requested_1280"]
        self.assertTrue(result["comparable"])
        self.assertFalse(result["journals_fully_visible_before"])
        self.assertTrue(result["journals_fully_visible_after"])
        self.assertEqual(result["fully_visible_journal_filters_delta"], 3)
        self.assertTrue(result["controls"]["source_filter"]["fully_visible_after"])

    def test_missing_controls_remain_missing_not_offscreen(self):
        doc = sample()
        doc["controls"]["ai_heading"] = {"present": False}
        self.write(entry={"baseline": doc})
        report = review(self.path)
        self.assertEqual(report["samples"]["baseline"]["controls"]["ai_heading"], {"present": False})
        self.assertNotIn("requested_1280", report["samples"])

    def test_refuse_bad_status_and_wrong_fixture(self):
        self.write(status="FAIL")
        with self.assertRaises(ValueError):
            review(self.path)
        self.write(broker="production")
        with self.assertRaises(ValueError):
            review(self.path)

    def test_refuse_inconsistent_geometry_and_nonfinite(self):
        bad = sample()
        bad["controls"]["journals_heading"] = control(visible=True)
        bad["controls"]["journals_heading"]["intersects_first_screen"] = False
        self.write(entry={"baseline": bad})
        with self.assertRaises(ValueError):
            review(self.path)
        bad = sample()
        bad["vertical_scroll_extent_viewports"] = float("nan")
        self.write(entry={"baseline": bad})
        with self.assertRaises(ValueError):
            review(self.path)

    def test_refuse_window_geometry_mismatch(self):
        self.write()
        earlier = review(self.path)
        self.write(entry={"baseline": sample(width=1400), "requested_1280": sample(width=1350)})
        comparisons = compare(earlier, review(self.path))["samples"]
        self.assertFalse(comparisons["requested_1280"]["comparable"])
        self.assertEqual(comparisons["requested_1280"]["reason"], "different_window_geometry")

    def test_cli_before_after_confidentiality_and_rejection(self):
        self.write()
        before = self.path.with_name("private-before.json")
        before.write_bytes(self.path.read_bytes())
        self.write(entry={"baseline": sample(width=1326), "requested_1280": sample(visible=True)})
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main([str(self.path), "--before", str(before)])
        self.assertEqual(result, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn(str(before), stdout.getvalue())
        self.assertNotIn("private.example", stdout.getvalue())
        self.assertTrue(json.loads(stdout.getvalue())["comparison"]["samples"]["requested_1280"]["comparable"])
        self.write(status="FAIL")
        with redirect_stdout(stdout := StringIO()), redirect_stderr(stderr := StringIO()):
            result = main([str(self.path)])
        self.assertEqual(result, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn(str(self.path), stderr.getvalue())

    def test_refuse_symlink_and_bool_as_number(self):
        self.write()
        link = self.path.with_name("symbolic.json")
        link.symlink_to(self.path)
        with self.assertRaises(ValueError):
            review(link)
        bad = sample()
        bad["window_width"] = True
        self.write(entry={"baseline": bad})
        with self.assertRaises(ValueError):
            review(self.path)


if __name__ == "__main__":
    unittest.main()
