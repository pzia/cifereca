import datetime as dt
import tempfile
import unittest
from unittest.mock import patch
from scripts import backlog_compaction as c

NOW = dt.datetime(2026, 11, 27, 12, 0, tzinfo=dt.timezone.utc)
HEAD = "a" * 40
STAGE = "b" * 40

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
            return [dict(x) for x in self.comments[:100]]
        if path == "/issues/123/comments?per_page=100&page=2":
            return [dict(x) for x in self.comments[100:]]
        raise AssertionError("unexpected path")

    def replace_body(self, n, body):
        self.writes.append(("body", n))
        self.issue["body"] = body

    def mark(self, n, body):
        self.writes.append(("mark", n))
        value = {"id": 100 + len(self.comments), "body": body,
                 "user": {"id": 41898282, "login": "github-actions[bot]"},
                 "html_url": "https://github.com/pzia/cifereca/issues/123#issuecomment-" + str(100 + len(self.comments)),
                 "created_at": "2026-11-27T12:00:00Z", "updated_at": "2026-11-27T12:00:00Z"}
        self.comments.append(value)
        self.issue["comments"] += 1
        return dict(value)

    def replace_comment(self, cid, body):
        self.writes.append(("comment", cid))
        next(c for c in self.comments if c["id"] == cid)["body"] = body

class Stage:
    def __init__(self):
        self.commit = None
        self.raw = None
        self.published = 0

    def remote_head(self, n):
        return self.commit

    def publish(self, n, raw):
        self.published += 1
        self.raw, self.commit = raw, STAGE
        return STAGE

    def read_remote(self, n, commit):
        c.require(self.commit == commit, "staging changed")
        return self.raw

    def purge(self, n, commit):
        self.commit = None

class Tests(unittest.TestCase):
    def setUp(self):
        self.api = API()

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


    def consolidation_fixture(self):
        event = self.staging_issue()
        self.api.issue["body"] = "Evidence #42 " * 100 + "\n<!-- backlog-staging-v1: none ; public -->"
        event = dict(self.api.issue)
        stage = Stage()
        return event, stage

    def execute(self, event, stage):
        original = self.api.get
        with patch.object(self.api, "get", side_effect=lambda path: [] if "/sub_issues?" in path else original(path)):
            return c.consolidate(self.api, stage, event, HEAD, clock=lambda: NOW)

    def test_remote_roundtrip_and_replay(self):
        event, stage = self.consolidation_fixture()
        original_comment = self.api.comments[0]["body"]
        report = self.execute(event, stage)
        self.assertEqual(report["status"], "STAGED_AND_COMPACTED")
        self.assertGreater(report["context_gain"]["bytes"], 0)
        self.assertEqual(c.strict_json(stage.raw)["snapshots"][0]["comments"][0]["body"], original_comment)
        self.assertIn("#42", self.api.issue["body"])
        self.assertEqual(self.api.comments[0]["id"], 8)
        self.assertEqual(self.api.writes, [("mark", 123), ("body", 123), ("comment", 8), ("mark", 123)])
        with self.assertRaises(c.Refusal):
            self.execute(event, stage)

    def test_remote_verification_failure_no_mutation(self):
        event, stage = self.consolidation_fixture()
        with patch.object(stage, "read_remote", return_value=b"changed"):
            with self.assertRaisesRegex(c.Refusal, "staging mismatch"):
                self.execute(event, stage)
        self.assertEqual(self.api.writes, [])
        self.assertEqual(stage.commit, STAGE)

    def test_partial_failure_retains_stage(self):
        event, stage = self.consolidation_fixture()
        with patch.object(self.api, "replace_comment", side_effect=c.Refusal("failed")):
            with self.assertRaises(c.Refusal):
                self.execute(event, stage)
        self.assertEqual(stage.commit, STAGE)
        self.assertTrue(any(x["body"].startswith(c.START) for x in self.api.comments))
        self.assertFalse(any(x["body"].startswith(c.DONE) for x in self.api.comments))

    def test_short_evidence_kept(self):
        proof = "Preuve unique à conserver. " * 4
        self.assertEqual(c.compact_text(proof, 123), proof)

    def test_timestamp_only_after_verified_write(self):
        event, stage = self.consolidation_fixture()
        original = self.api.replace_comment
        def changed(cid, body):
            original(cid, body)
        with patch.object(self.api, "replace_comment", side_effect=changed):
            before = c.snapshot(self.api, 123)
            after = c.strict_json(c.encode(before))
            after["issue"]["updated_at"] = "2026-11-27T12:00:01Z"
            self.assertTrue(c.same_after_write(before, after))
            after["issue"]["title"] = "Changed"
            self.assertFalse(c.same_after_write(before, after))

    def test_receive_completed_without_replay(self):
        event, stage = self.consolidation_fixture()
        original_mark = self.api.mark
        def mark(n, body):
            if body.startswith(c.DONE):
                raise c.Refusal("simulated final failure")
            return original_mark(n, body)
        with patch.object(self.api, "mark", side_effect=mark):
            with self.assertRaises(c.Refusal):
                self.execute(event, stage)
        data = {"version":1,"p0":123,"stage_sha":STAGE,"manifest_sha256":c.sha256(stage.raw),
                "main_sha":HEAD,"reviewed":True}
        control = {"id":999,"number":999,"state":"closed","state_reason":"completed",
                   "title":c.RECEIVE_TITLE+"123","body":c.encode(data).decode(),
                   "comments":0,"labels":[],"assignees":[],"closed_at":"2026-11-27T12:00:00Z",
                   "user":{"id":c.OWNER_ID}}
        self.api.pilot = control
        original_get = self.api.get
        with patch.object(self.api, "get", side_effect=lambda path: [] if "/sub_issues?" in path else original_get(path)):
            before = len(self.api.writes)
            self.api.comments[0]["body"] = "unexpected edit"
            with self.assertRaisesRegex(c.Refusal, "incomplete or divergent"):
                c.receive_completed(self.api, stage, control, HEAD)
            self.assertEqual(len(self.api.writes), before)
            self.api.comments[0]["body"] = c.compact_text(c.strict_json(stage.raw)["snapshots"][0]["comments"][0]["body"],123)
            report = c.receive_completed(self.api, stage, control, HEAD)
            self.assertEqual(report["status"], "RECEIVED_ALREADY_COMPACTED")
            self.assertEqual(self.api.writes[before:], [("mark",123)])
            with self.assertRaises(c.Refusal):
                c.receive_completed(self.api, stage, control, HEAD)

    def test_capitalization_purge(self):
        event, stage = self.consolidation_fixture()
        result = self.execute(event, stage)
        evidence = b"Durable invariant #42"
        data = {"version":1,"p0":123,"stage_sha":STAGE,"manifest_sha256":result["manifest_sha256"],
                "main_sha":HEAD,"evidence":[{"path":"AGENTS.md","sha256":c.sha256(evidence)}],"semantic_review":True}
        control = {"id":999,"number":999,"state":"closed","state_reason":"completed","title":c.PURGE_TITLE+"123",
                   "body":c.encode(data).decode(),"comments":0,"labels":[],"assignees":[],
                   "closed_at":"2026-11-27T12:00:00Z","user":{"id":c.OWNER_ID}}
        self.api.pilot = control
        with self.assertRaisesRegex(c.Refusal, "evidence changed"):
            c.purge_capitalized(self.api, stage, control, HEAD, evidence_at=lambda *_: b"bad")
        self.assertEqual(stage.commit, STAGE)
        report = c.purge_capitalized(self.api, stage, control, HEAD, evidence_at=lambda *_: evidence)
        self.assertEqual(report["status"], "STAGING_REFERENCE_PURGED")
        self.assertIsNone(stage.commit)

    def test_pagination_and_duplicate(self):
        self.api.comments = [{"id":i+1,"body":"x"} for i in range(101)]
        self.api.issue["comments"] = 101
        self.assertEqual(len(c.snapshot(self.api,123)["comments"]),101)
        self.api.comments[-1]["id"] = 1
        with self.assertRaises(c.Refusal):
            c.snapshot(self.api,123)

    def test_duplicate_json(self):
        with self.assertRaises(c.Refusal):
            c.strict_json(b'{"x":1,"x":2}')

    def test_git_transport_and_lease(self):
        with tempfile.TemporaryDirectory() as remote_dir, tempfile.TemporaryDirectory() as writer_dir:
            remote = c.GitStage(remote_dir)
            writer = c.GitStage(writer_dir)
            writer.remote = remote_dir
            raw = b'{"version":1,"p0":123}'
            commit = writer.publish(123, raw)
            self.assertEqual(writer.read_remote(123, commit), raw)
            self.assertEqual(writer.remote_head(123), commit)
            with self.assertRaises(c.Refusal):
                writer.publish(123, raw)
            with self.assertRaises(c.Refusal):
                writer.purge(123, "a" * 40)
            writer.purge(123, commit)
            self.assertIsNone(writer.remote_head(123))

    def test_no_gain_refuses_before_staging(self):
        event, stage = self.consolidation_fixture()
        self.api.issue["body"] = event["body"] = "Evidence\n<!-- backlog-staging-v1: none ; public -->"
        self.api.comments[0]["body"] = "Unique"
        with self.assertRaisesRegex(c.Refusal, "no positive contextual gain"):
            self.execute(event, stage)
        self.assertIsNone(stage.commit)
        self.assertEqual(self.api.writes, [])

    def test_ref_injection(self):
        with self.assertRaises(c.Refusal):
            c.stage_ref("1/../../main")

    def test_comment_mutation_endpoint(self):
        api = c.WriteAPI("synthetic-token")
        with patch.object(api.opener,"open") as opener:
            response = opener.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b'{}'
            api.replace_comment(8, "stub")
            req = opener.call_args.args[0]
            self.assertEqual((req.method,req.full_url,c.strict_json(req.data)),
                             ("PATCH",c.API+"/issues/comments/8",{"body":"stub"}))
            with self.assertRaises(c.Refusal):
                api.replace_comment("8/../../main","stub")

if __name__ == "__main__":
    unittest.main()
