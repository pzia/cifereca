import datetime as dt
import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from scripts import backlog_compaction as c

NOW = dt.datetime(2026, 11, 27, 12, 0, tzinfo=dt.timezone.utc)
HEAD = "a" * 40
BASE = c.BASELINE

class API:
    def __init__(self):
        self.issue = {"number": 123, "state": "closed", "body": "Evidence #42",
                      "comments": 1, "labels": [{"name": "P2"}]}
        self.comments = [{"id": 8, "body": "Repeated text " * 1000}]
        self.pilot = {"number": 999, "state": "open", "body": "", "comments": 0}
        self.retained = {"number": 42, "state": "open", "body": "Canonical evidence #42", "comments": 0}
        self.pilot_comments = []
        self.calls = []
        self.writes = []
        self.main_sha = HEAD

    def get(self, path):
        self.calls.append(path)
        if path == "/git/ref/heads/main":
            return {"ref": "refs/heads/main", "object": {"sha": self.main_sha}}
        if path == "/issues/123":
            return dict(self.issue)
        if path == "/issues/999":
            return dict(self.pilot)
        if path == "/issues/42":
            return dict(self.retained)
        if path == "/issues/999/comments?per_page=100&page=1":
            return list(self.pilot_comments)
        if path == "/issues/42/comments?per_page=100&page=1":
            return []
        if path == "/issues/123/comments?per_page=100&page=1":
            return list(self.comments[:100])
        if path == "/issues/123/comments?per_page=100&page=2":
            return list(self.comments[100:])
        raise AssertionError("unexpected path")

    def consume(self, pilot, marker):
        self.writes.append(("consume", pilot))
        comment = {"id": 88, "body": marker}
        self.pilot_comments.append(comment)
        self.pilot["comments"] += 1
        return dict(comment)

    def replace_body(self, n, body):
        self.writes.append(("body", n))
        self.issue["body"] = body

    def delete_comment(self, comment_id):
        self.writes.append(("delete", comment_id))
        self.comments = [c for c in self.comments if c["id"] != comment_id]
        self.issue["comments"] = len(self.comments)

def docs(ref):
    if ref not in (HEAD, BASE):
        raise c.Refusal("invalid document SHA")
    return {"AGENTS.md": "Invariant #1"}

class Tests(unittest.TestCase):
    def setUp(self):
        self.api = API()
        self.plan = {"version": 2, "repository": c.REPO, "pilot_issue": 999,
                     "main_sha": HEAD, "baseline_sha": HEAD, "closure_p0": None,
                     "created_at": "2026-11-27T11:00:00Z",
                     "expires_at": "2026-11-27T13:00:00Z",
                     "semantic_review": "Reviewed separately; not certified by the CLI.",
                     "execution_review": {"corpus_quiescent": True, "known_inbound_references": True, "global_gain": True},
                     "retained_issues": [],
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
        original = dict(self.api.issue)
        for key, value in (("state", "open"), ("pull_request", {})):
            with self.subTest(key=key):
                self.api.issue = dict(original, **{key: value})
                with self.assertRaisesRegex(c.Refusal, "unexpected issue state" if key == "state" else "not a repository issue"):
                    self.validate()

    def test_closure_requires_closed_p0_in_corpus(self):
        self.plan.update(closure_p0=123, baseline_sha=HEAD)
        with self.assertRaisesRegex(c.Refusal, "closure parent must be closed P0"):
            self.validate()
        self.api.issue["labels"] = [{"name": "P0"}]
        self.plan["issues"][0]["snapshot_sha256"] = c.fingerprint(c.snapshot(self.api, 123))
        self.assertEqual(self.validate()["closure_p0"], 123)
        self.plan["closure_p0"] = 456
        with self.assertRaisesRegex(c.Refusal, "closure P0 outside bounded corpus"):
            self.validate()

    def test_closure_does_not_recredit_initial_document_baseline(self):
        self.plan.update(closure_p0=123, baseline_sha=BASE)
        with self.assertRaisesRegex(c.Refusal, "plan context mismatch"):
            self.validate()

    def test_closure_retained_parent_must_be_closed(self):
        self.plan.update(closure_p0=42, baseline_sha=HEAD)
        self.api.retained["labels"] = [{"name": "P0"}]
        self.plan["retained_issues"] = [{"number": 42, "snapshot_sha256": c.fingerprint(c.snapshot(self.api, 42, closed=None))}]
        with self.assertRaisesRegex(c.Refusal, "closure parent must be closed P0"):
            self.validate()
        self.api.retained["state"] = "closed"
        self.plan["retained_issues"][0]["snapshot_sha256"] = c.fingerprint(c.snapshot(self.api, 42, closed=None))
        self.assertEqual(self.validate()["closure_p0"], 42)

    def test_wrong_baseline(self):
        self.plan["baseline_sha"] = BASE
        with self.assertRaises(c.Refusal):
            self.validate()

    def test_body_only_with_retained_canonical_evidence(self):
        self.plan["issues"][0].update(body_after="Consolidated", remove_comments=[])
        self.plan["retained_issues"] = [{"number": 42, "snapshot_sha256": c.fingerprint(c.snapshot(self.api, 42, closed=None))}]
        result = self.validate()
        self.assertEqual(result["retained_issues"], [42])
        self.assertFalse(result["destruction_enabled"])
        self.api.retained["body"] = "Changed"
        with self.assertRaisesRegex(c.Refusal, "retained issue drift"):
            self.validate()

    def test_reference_to_removed_comment(self):
        link = "https://github.com/pzia/cifereca/issues/123#issuecomment-8"
        self.api.retained["body"] += " " + link
        self.plan["retained_issues"] = [{"number": 42, "snapshot_sha256": c.fingerprint(c.snapshot(self.api, 42, closed=None))}]
        with self.assertRaisesRegex(c.Refusal, "known reference to deleted comment"):
            self.validate()

    def test_pilot_comments_count_as_overhead(self):
        first = self.validate()
        self.api.pilot_comments = [{"id": 88, "body": "é" * 50}]
        self.api.pilot["comments"] = 1
        second = self.validate()
        self.assertEqual(second["projected_after_with_plan"]["bytes"] - first["projected_after_with_plan"]["bytes"], 100)

    def test_noop_body_only(self):
        self.plan["issues"][0].update(remove_comments=[])
        with self.assertRaisesRegex(c.Refusal, "empty change proposal"):
            self.validate()

    def test_second_snapshot_drift(self):
        original = self.api.get
        calls = 0
        def get(path):
            nonlocal calls
            if path == "/issues/123/comments?per_page=100&page=1":
                calls += 1
                if calls == 2:
                    self.api.comments[0]["body"] += " new"
            return original(path)
        with patch.object(self.api, "get", side_effect=get):
            with self.assertRaisesRegex(c.Refusal, "snapshot drift"):
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

    def test_local_apply_refused_before_any_network(self):
        with patch.dict(c.os.environ, {}, clear=True), patch.object(c, "ReadOnlyAPI") as api, patch.object(c, "head") as head:
            with redirect_stderr(io.StringIO()):
                self.assertEqual(c.main(["apply"]), 2)
            api.assert_not_called()
            head.assert_not_called()

    def apply(self):
        raw = c.encode(self.plan)
        self.api.pilot["body"] = raw.decode("utf-8")
        return c.apply_plan(raw, c.sha256(raw), 999, HEAD, self.api, "123",
                            docs_at=docs, clock=lambda: NOW)

    def test_apply_and_replay(self):
        self.plan["issues"][0]["body_after"] = "Consolidated Evidence #42"
        result = self.apply()
        self.assertEqual(self.api.writes, [("consume", 999), ("body", 123), ("delete", 8)])
        self.assertEqual(self.api.comments, [])
        self.assertEqual(self.api.issue["labels"], [{"name": "P2"}])
        self.assertEqual(result["status"], "APPLIED_BOUNDED_PLAN")
        self.assertTrue(result["journal_included"])
        self.assertFalse(result["semantic_equivalence_proven"])
        self.assertGreater(result["actual_bounded_gain"]["bytes"], 0)
        self.assertNotIn("Repeated text", str(result))
        with self.assertRaisesRegex(c.Refusal, "pilot already consumed"):
            self.apply()
        self.assertEqual(len(self.api.writes), 3)

    def test_apply_main_drift(self):
        self.api.main_sha = BASE
        with self.assertRaisesRegex(c.Refusal, "main changed"):
            self.apply()
        self.assertEqual(self.api.writes, [])

    def test_apply_requires_review(self):
        self.plan["execution_review"]["corpus_quiescent"] = False
        with self.assertRaisesRegex(c.Refusal, "missing Backlog execution review"):
            self.apply()
        self.assertEqual(self.api.writes, [])

    def test_apply_partial_failure_consumes_before_delete(self):
        self.plan["issues"][0]["body_after"] = "Consolidated Evidence #42"
        with patch.object(self.api, "delete_comment", side_effect=c.Refusal("synthetic failure")):
            with self.assertRaisesRegex(c.Refusal, "synthetic failure"):
                self.apply()
        self.assertEqual(self.api.writes, [("consume", 999), ("body", 123)])
        self.assertEqual(len(self.api.comments), 1)
        with self.assertRaisesRegex(c.Refusal, "pilot already consumed"):
            self.apply()

    def test_apply_drift_after_marker(self):
        consume = self.api.consume
        def changed(pilot, marker):
            value = consume(pilot, marker)
            self.api.issue["labels"] = []
            return value
        with patch.object(self.api, "consume", side_effect=changed):
            with self.assertRaisesRegex(c.Refusal, "corpus changed"):
                self.apply()
        self.assertEqual(self.api.writes, [("consume", 999)])

    def test_apply_ambiguous_marker_failure(self):
        consume = self.api.consume
        def lost(pilot, marker):
            consume(pilot, marker)
            raise c.Refusal("synthetic timeout")
        with patch.object(self.api, "consume", side_effect=lost):
            with self.assertRaisesRegex(c.Refusal, "synthetic timeout"):
                self.apply()
        self.assertEqual(self.api.writes, [("consume", 999)])
        with self.assertRaisesRegex(c.Refusal, "pilot already consumed"):
            self.apply()

    def test_apply_unexpected_body_metadata_change(self):
        self.plan["issues"][0]["body_after"] = "Consolidated Evidence #42"
        replace = self.api.replace_body
        def changed(n, body):
            replace(n, body)
            self.api.issue["labels"] = []
        with patch.object(self.api, "replace_body", side_effect=changed):
            with self.assertRaisesRegex(c.Refusal, "unexpected post-mutation state"):
                self.apply()
        self.assertEqual(self.api.writes, [("consume", 999), ("body", 123)])
        self.assertEqual(len(self.api.comments), 1)

    def test_apply_context(self):
        env = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
               "GITHUB_REPOSITORY": c.REPO, "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": HEAD,
               "GITHUB_RUN_ATTEMPT": "1", "GITHUB_ACTOR": "pzia", "GITHUB_ACTOR_ID": "11092036",
               "GITHUB_TRIGGERING_ACTOR": "pzia", "GITHUB_RUN_ID": "123",
               "GITHUB_WORKFLOW_REF": c.REPO + "/.github/workflows/backlog-compaction.yml@refs/heads/main"}
        self.assertEqual(c.apply_context(env, HEAD), "123")
        for key in env:
            with self.subTest(key=key), self.assertRaises(c.Refusal):
                c.apply_context(dict(env, **{key: "invalid"}), HEAD)

    def test_mutation_endpoint_injection(self):
        api = c.WriteAPI("synthetic-token")
        with patch.object(api.opener, "open") as opener:
            for call in (lambda: api.delete_comment("8/../1"),
                         lambda: api.replace_body(0, "x"), lambda: api.consume(999, "wrong")):
                with self.assertRaises(c.Refusal):
                    call()
            opener.assert_not_called()

    def test_apply_expiration_after_consumption(self):
        raw = c.encode(self.plan)
        self.api.pilot["body"] = raw.decode("utf-8")
        expired = NOW + dt.timedelta(hours=2)
        clock = iter([NOW, NOW, NOW, expired])
        with self.assertRaisesRegex(c.Refusal, "plan expired during execution"):
            c.apply_plan(raw, c.sha256(raw), 999, HEAD, self.api, "123",
                         docs_at=docs, clock=lambda: next(clock))
        self.assertEqual(self.api.writes, [("consume", 999)])

    def test_apply_no_positive_gain(self):
        self.api.comments[0]["body"] = "tiny"
        self.plan["issues"][0]["snapshot_sha256"] = c.fingerprint(c.snapshot(self.api, 123))
        with self.assertRaisesRegex(c.Refusal, "no positive bounded projected gain"):
            self.apply()
        self.assertEqual(self.api.writes, [])

    def test_apply_removal_bound(self):
        self.api.comments = [{"id": i, "body": "x" * 1000} for i in range(1, 12)]
        self.api.issue["comments"] = 11
        self.plan["issues"][0].update(snapshot_sha256=c.fingerprint(c.snapshot(self.api, 123)),
                                      remove_comments=list(range(1, 12)))
        with self.assertRaisesRegex(c.Refusal, "too many comment removals"):
            self.apply()
        self.assertEqual(self.api.writes, [])

    def test_mutation_http_permission_failure_and_no_leak(self):
        api = c.WriteAPI("synthetic-secret")
        err = c.urllib.error.HTTPError(c.API + "/issues/123", 403, "secret response", {}, None)
        with patch.object(api.opener, "open", side_effect=err) as opener:
            with self.assertRaisesRegex(c.Refusal, "GitHub request failed; do not retry mutations") as exc:
                api.replace_body(123, "private synthesis")
            self.assertNotIn("secret", str(exc.exception))
            self.assertNotIn("synthesis", str(exc.exception))
            opener.assert_called_once()

    def test_mutation_http_methods_and_payload(self):
        api = c.WriteAPI("synthetic-secret")
        with patch.object(api.opener, "open") as opener:
            response = opener.return_value.__enter__.return_value
            response.status = 201
            response.read.return_value = b'{"id":88}'
            marker = c.CONSUMED + "a" * 64
            api.consume(999, marker)
            req = opener.call_args.args[0]
            self.assertEqual((req.method, req.full_url, c.strict_json(req.data)),
                             ("POST", c.API + "/issues/999/comments", {"body": marker}))
            response.status = 200
            api.replace_body(123, "synthesis")
            req = opener.call_args.args[0]
            self.assertEqual((req.method, req.full_url, c.strict_json(req.data)),
                             ("PATCH", c.API + "/issues/123", {"body": "synthesis"}))
            response.status = 204
            response.read.return_value = b""
            api.delete_comment(8)
            req = opener.call_args.args[0]
            self.assertEqual((req.method, req.full_url, req.data),
                             ("DELETE", c.API + "/issues/comments/8", None))

    def staging_issue(self):
        issue = dict(self.api.issue)
        issue.update(id=100, title="Synthetic P0", body="Evidence\n<!-- backlog-staging-v1: none ; public -->",
                     state_reason="completed", created_at="2026-11-27T11:00:00Z",
                     updated_at="2026-11-27T12:00:00Z", closed_at="2026-11-27T12:00:00Z",
                     html_url="https://github.com/pzia/cifereca/issues/123",
                     labels=[{"name": "P0"}], sub_issues_summary={"total": 0},
                     user={"login": "pzia", "id": 11092036, "email": "not-exported"})
        self.api.issue = issue
        self.api.comments[0].update(user={"login": "pzia", "id": 11092036},
            html_url="https://github.com/pzia/cifereca/issues/123#issuecomment-8",
            created_at="2026-11-27T11:00:00Z", updated_at="2026-11-27T11:00:00Z")
        return dict(issue)

    def test_staging_orphan_roundtrip(self):
        event = self.staging_issue()
        original = self.api.get
        def get(path):
            return [] if "/sub_issues?" in path else original(path)
        with patch.object(self.api, "get", side_effect=get):
            manifest = c.collect_closure(self.api, event, HEAD)
        self.assertNotIn("not-exported", str(manifest))
        self.assertEqual(manifest["snapshots"][0]["comments"][0]["body"], self.api.comments[0]["body"])
        result = c.local_stage_roundtrip(manifest)
        self.assertEqual(result["status"], "STAGING_LOCAL_ROUNDTRIP_ONLY")
        self.assertFalse(result["destruction_enabled"])
        self.assertFalse(result["remote_staging_verified"])
        self.assertEqual(self.api.writes, [])

    def test_staging_refuses_ambiguous_or_missing_scope(self):
        event = self.staging_issue()
        for body in ("No scope", event["body"] + "\n" + event["body"]):
            with self.subTest(body=body), self.assertRaisesRegex(c.Refusal, "scope"):
                c.collect_closure(self.api, dict(event, body=body), HEAD)
        self.assertEqual(self.api.writes, [])

    def test_staging_refuses_possible_secret(self):
        event = self.staging_issue()
        self.api.comments[0]["body"] = "ghp_" + "X" * 36
        original = self.api.get
        with patch.object(self.api, "get", side_effect=lambda path: [] if "/sub_issues?" in path else original(path)):
            with self.assertRaisesRegex(c.Refusal, "possible secret"):
                c.collect_closure(self.api, event, HEAD)
        self.assertEqual(self.api.writes, [])

    def test_staging_refuses_unlisted_native_child(self):
        event = self.staging_issue()
        original = self.api.get
        with patch.object(self.api, "get", side_effect=lambda path: [{"number": 124}] if "/sub_issues?" in path else original(path)):
            with self.assertRaisesRegex(c.Refusal, "unresolved native"):
                c.collect_closure(self.api, event, HEAD)
        self.assertEqual(self.api.writes, [])

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
