import datetime as dt
import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from scripts import backlog_compaction as c

NOW = dt.datetime(2026, 11, 27, 12, 0, tzinfo=dt.timezone.utc)
HEAD = "a" * 40
BASE = "b" * 40

class API:
    def __init__(self):
        self.issue = {"number": 123, "state": "closed", "body": "Evidence #42",
                      "comments": 1, "labels": [{"name": "P2"}]}
        self.comments = [{"id": 8, "body": "Repeated text " * 1000}]
        self.pilot = {"number": 999, "state": "open", "body": "", "comments": 0}
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        if path == "/issues/123":
            return dict(self.issue)
        if path == "/issues/999":
            return dict(self.pilot)
        if path == "/issues/123/comments?per_page=100&page=1":
            return list(self.comments[:100])
        if path == "/issues/123/comments?per_page=100&page=2":
            return list(self.comments[100:])
        raise AssertionError("unexpected path")

def docs(ref):
    if ref not in (HEAD, BASE):
        raise c.Refusal("invalid document SHA")
    return {"AGENTS.md": "Invariant #1"}

class Tests(unittest.TestCase):
    def setUp(self):
        self.api = API()
        self.plan = {"version": 1, "repository": c.REPO, "pilot_issue": 999,
                     "main_sha": HEAD, "baseline_sha": BASE,
                     "created_at": "2026-11-27T11:00:00Z",
                     "expires_at": "2026-11-27T13:00:00Z",
                     "semantic_review": "Reviewed separately; not certified by the CLI.",
                     "issues": [{"number": 123, "snapshot_sha256": c.fingerprint(c.snapshot(self.api, 123)),
                                 "body_after": "Evidence #42", "remove_comments": [8],
                                 "rationale": "Repeated text"}]}

    def validate(self, expected=None):
        raw = c.encode(self.plan)
        self.api.pilot["body"] = raw.decode("utf-8")
        return c.validate_plan(raw, expected or c.sha256(raw), 999, HEAD,
                               self.api, docs_at=docs, now=NOW)

    def test_structural_validation_is_not_permission(self):
        result = self.validate()
        self.assertEqual(result["status"], "STRUCTURAL_VALIDATION_ONLY")
        self.assertGreater(result["projected_gain"]["bytes"], 0)
        self.assertFalse(result["destruction_enabled"])
        self.assertFalse(result["journal_included"])
        self.assertFalse(result["semantic_equivalence_proven"])
        self.assertNotIn("Repeated text", str(result))

    def test_tampering(self):
        with self.assertRaises(c.Refusal):
            self.validate("0" * 64)

    def test_expired(self):
        self.plan["expires_at"] = "2026-11-27T11:59:59Z"
        with self.assertRaises(c.Refusal):
            self.validate()

    def test_wrong_repository(self):
        self.plan["repository"] = "other/repo"
        with self.assertRaises(c.Refusal):
            self.validate()

    def test_wrong_head(self):
        self.plan["main_sha"] = BASE
        with self.assertRaises(c.Refusal):
            self.validate()

    def test_open_or_pr(self):
        for key, value in (("state", "open"), ("pull_request", {})):
            self.api.issue[key] = value
            with self.assertRaises(c.Refusal):
                self.validate()

    def test_unknown_field(self):
        self.plan["command"] = "rm -rf /"
        with self.assertRaises(c.Refusal):
            self.validate()

    def test_duplicate_json_key(self):
        with self.assertRaises(c.Refusal):
            c.strict_json(b'{"version":1,"version":2}')

    def test_unknown_comment(self):
        self.plan["issues"][0]["remove_comments"] = [999]
        with self.assertRaises(c.Refusal):
            self.validate()

    def test_reference_loss(self):
        self.plan["issues"][0]["body_after"] = "Evidence"
        with self.assertRaises(c.Refusal):
            self.validate()

    def test_drift(self):
        self.api.comments[0]["body"] += "new unique evidence"
        with self.assertRaises(c.Refusal):
            self.validate()

    def test_pagination(self):
        self.api.comments = [{"id": i + 1, "body": "x"} for i in range(101)]
        self.api.issue["comments"] = 101
        self.assertEqual(len(c.snapshot(self.api, 123)["comments"]), 101)
        self.api.issue["comments"] = 102
        with self.assertRaises(c.Refusal):
            c.snapshot(self.api, 123)

    def test_duplicate_comment(self):
        self.api.comments.append(dict(self.api.comments[0]))
        self.api.issue["comments"] = 2
        with self.assertRaises(c.Refusal):
            c.snapshot(self.api, 123)

    def test_apply_always_refused_before_any_network(self):
        with patch.object(c, "ReadOnlyAPI") as api, patch.object(c, "head") as head:
            with redirect_stderr(io.StringIO()):
                self.assertEqual(c.main(["apply"]), 2)
            api.assert_not_called()
            head.assert_not_called()

    def test_api_path_injection(self):
        api = c.ReadOnlyAPI("synthetic-token")
        with patch.object(api.opener, "open") as opener:
            with self.assertRaises(c.Refusal):
                api.get("/issues/1/../../actions/workflows")
            opener.assert_not_called()

    def test_utf8_metrics_and_duplicate_candidates(self):
        self.assertEqual(c.metrics(["é"])["bytes"], 2)
        text = "a" * 100
        report = c.doc_report({"a": text, "b": text})
        self.assertEqual(report["duplicate_paragraph_candidates"][0]["copies"], 2)
        self.assertNotIn(text, str(report))

if __name__ == "__main__":
    unittest.main()
