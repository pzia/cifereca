import copy
import unittest
from unittest.mock import patch

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

    def test_no_apply_command(self):
        with patch.object(engine, "ReadOnlyAPI") as reader, self.assertRaises(SystemExit):
            local.main(["plan", "--master", "8249", "--apply"])
        reader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
