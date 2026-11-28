import copy
import unittest
from unittest.mock import MagicMock, patch

from scripts import backlog_local as local
from scripts import backlog_compaction as engine


class LocalReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {
            "p0": 100, "master": 99,
            "snapshots": [
                {"issue": {"number": 100, "body": "Anchor"}, "comments": []},
                {"issue": {"number": 101, "body": "A" * 300},
                 "comments": [{"id": 8, "body": "B" * 300}]},
                {"issue": {"number": 102, "body": "C" * 300}, "comments": []},
            ],
        }
        self.originals = {s["issue"]["number"]: s for s in self.manifest["snapshots"]}
        self.digest = "a" * 64
        self.commit = "b" * 40

    def observed(self, prefix):
        result = copy.deepcopy(self.originals)
        for n, cid, body in engine.lot_plan(self.manifest)[:prefix]:
            if cid is None:
                result[n]["issue"]["body"] = body
            else:
                next(c for c in result[n]["comments"] if c["id"] == cid)["body"] = body
        return result

    def test_exact_prefix(self):
        for prefix in range(4):
            self.assertEqual(local.infer_prefix(self.manifest, self.observed(prefix)), prefix)

    def test_hole_refused(self):
        observed = self.observed(2)
        observed[101]["issue"]["body"] = self.originals[101]["issue"]["body"]
        with self.assertRaises(engine.Refusal):
            local.infer_prefix(self.manifest, observed)

    def test_divergence_refused(self):
        observed = self.observed(1)
        observed[101]["comments"][0]["body"] = "Unexpected"
        with self.assertRaises(engine.Refusal):
            local.infer_prefix(self.manifest, observed)

    def test_missing_comment_refused(self):
        observed = self.observed(1)
        observed[101]["comments"] = []
        with self.assertRaises(engine.Refusal):
            local.infer_prefix(self.manifest, observed)

    def test_progress_exact_sequence(self):
        start = engine.START + self.digest + " " + self.commit
        p10 = engine.progress_marker(self.digest, self.commit, 10)
        p20 = engine.progress_marker(self.digest, self.commit, 20)
        self.assertEqual(local.parse_progress([start], self.digest, self.commit, 23), 0)
        self.assertEqual(local.parse_progress([start, p10, p20], self.digest, self.commit, 23), 20)
        for markers in ([start, p20], [start, p10, p10],
                        [start, engine.DONE + self.digest + " " + self.commit]):
            with self.assertRaises(engine.Refusal):
                local.parse_progress(markers, self.digest, self.commit, 23)

    def test_fake_bot_refused(self):
        snap = {"comments": [{"body": engine.START + self.digest + " " + self.commit,
                               "user": {"login": "pzia", "id": engine.OWNER_ID}}]}
        with self.assertRaises(engine.Refusal):
            local.journal(snap)

    def test_local_owner_journal_explicit_only(self):
        snap = {"comments": [{"body": engine.START + self.digest + " " + self.commit,
                               "user": engine.OWNER_AUTHOR}]}
        with self.assertRaises(engine.Refusal):
            local.journal(snap)
        self.assertEqual(len(local.journal(snap, local.LOCAL_AUTHORS)), 1)

    def test_continue_uses_shared_engine_and_journal_only_checkpoint(self):
        api, stage = MagicMock(spec=engine.WriteAPI), MagicMock(spec=engine.GitStage)
        raw = engine.encode(self.manifest)
        digest = engine.sha256(raw)
        stage.read_remote.return_value = raw
        stage.remote_head.return_value = self.commit
        receipt = {"status": "READ_ONLY_PREFIX_VERIFIED", "master": 99,
                   "stage_sha": self.commit, "manifest_sha256": digest,
                   "cursor": 0, "applied": 10}
        results = [{"status": "LOT_PENDING", "cursor": 10},
                   {"status": "STAGED_AND_COMPACTED", "cursor": 13}]
        with patch.object(local, "local_checkout"), \
                patch.object(local, "ensure_no_closure_runs"), \
                patch.object(engine, "main_guard"), \
                patch.object(local, "reconcile", return_value=receipt), \
                patch.object(engine, "apply_lot_tranche", side_effect=results) as apply, \
                patch.object(engine, "verify_compacted_corpus") as verify:
            result = local.continue_local(api, stage, 100, 99, self.commit, digest, "c" * 40)
        api.authenticated_owner.assert_called_once()
        stage.execution_lock.assert_called_once()
        self.assertEqual([call.args[5:7] for call in apply.call_args_list], [(0, 10), (10, 10)])
        self.assertTrue(all(call.kwargs["writer_author"] == engine.OWNER_AUTHOR
                            for call in apply.call_args_list))
        verify.assert_called_once()
        self.assertEqual(result["cursor"], 13)
        self.assertFalse(result["purge_enabled"])
        self.assertIsNone(api.write_guard)

    def test_local_scope_mismatch_never_applies(self):
        api, stage = MagicMock(spec=engine.WriteAPI), MagicMock(spec=engine.GitStage)
        receipt = {"status": "READ_ONLY_PREFIX_VERIFIED", "master": 98,
                   "stage_sha": self.commit, "manifest_sha256": self.digest}
        with patch.object(local, "local_checkout"), \
                patch.object(local, "ensure_no_closure_runs"), \
                patch.object(engine, "main_guard"), \
                patch.object(local, "reconcile", return_value=receipt), \
                patch.object(engine, "apply_lot_tranche") as apply:
            with self.assertRaisesRegex(engine.Refusal, "scope mismatch"):
                local.continue_local(api, stage, 100, 99, self.commit, self.digest, "c" * 40)
        apply.assert_not_called()
        self.assertIsNone(api.write_guard)

    def test_local_apply_flag_required(self):
        with patch.object(engine, "WriteAPI") as writer, self.assertRaises(SystemExit):
            local.main(["continue", "--anchor", "100", "--master", "99",
                        "--stage-sha", self.commit, "--manifest-sha256", self.digest,
                        "--expect-main", "c" * 40])
        writer.assert_not_called()

    def test_no_apply_command(self):
        with patch.object(engine, "ReadOnlyAPI") as reader, self.assertRaises(SystemExit):
            local.main(["plan", "--master", "8249", "--apply"])
        reader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
