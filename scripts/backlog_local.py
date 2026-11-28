#!/usr/bin/env python3
"""Local inventory, reconciliation and explicitly authorized staged-lot continuation (#8207)."""
import argparse
import json
import os
import re
import sys
import tempfile

if __package__:
    from . import backlog_compaction as engine
else:
    import backlog_compaction as engine

BOT = engine.BOT_AUTHOR
LOCAL_AUTHORS = (BOT, engine.OWNER_AUTHOR)


def main_sha(api):
    ref = api.get("/git/ref/heads/main")
    engine.require(ref.get("ref") == "refs/heads/main", "invalid main ref")
    return engine.checked_sha(ref["object"]["sha"])


def journal(snap, authors=(BOT,)):
    markers = [c for c in snap["comments"]
               if c["body"].startswith("BACKLOG-STAGING-")]
    engine.require(all({k: c["user"][k] for k in BOT} in authors
                       for c in markers), "unauthenticated staging journal")
    return [c["body"] for c in markers]


def parse_progress(markers, digest, commit, total):
    engine.require(bool(markers) and markers[0] == engine.START + digest + " " + commit,
                   "missing exact START")
    cursor = (len(markers) - 1) * engine.LOT_BATCH
    expected = [markers[0]] + [
        engine.progress_marker(digest, commit, i)
        for i in range(engine.LOT_BATCH, cursor + 1, engine.LOT_BATCH)]
    engine.require(markers == expected and cursor < total, "not a resumable progress journal")
    return cursor


def infer_prefix(manifest, observed):
    """Only accept contiguous exact before/after texts, not stub heuristics."""
    originals = {s["issue"]["number"]: s for s in manifest["snapshots"]}
    def value(snap, cid):
        if cid is None:
            return snap["issue"]["body"]
        matches = [c["body"] for c in snap["comments"] if c["id"] == cid]
        engine.require(len(matches) == 1, "mutation target missing or duplicated")
        return matches[0]
    applied = 0
    pending = False
    for n, cid, new in engine.lot_plan(manifest):
        old = value(originals[n], cid)
        actual = value(observed[n], cid)
        engine.require(actual == old or actual == new, "divergent mutation text")
        if actual == new:
            engine.require(not pending, "non-contiguous mutation prefix")
            applied += 1
        else:
            pending = True
    return applied


def reconcile(api, stage, anchor, current, authors=LOCAL_AUTHORS):
    snap = engine.snapshot(api, anchor)
    markers = journal(snap, authors)
    engine.require(bool(markers), "no staging journal")
    match = re.fullmatch(re.escape(engine.START) + r"([0-9a-f]{64}) ([0-9a-f]{40})", markers[0])
    engine.require(match is not None, "invalid START")
    digest, commit = match.groups()
    raw = stage.read_remote(anchor, commit)
    engine.require(engine.sha256(raw) == digest, "staging digest mismatch")
    manifest = engine.strict_json(raw)
    engine.require(manifest["version"] == 1 and manifest["repository"] == engine.REPO
                   and manifest["p0"] == anchor and "master" in manifest, "invalid historical manifest")
    numbers = engine.issue_numbers([x["issue"]["number"] for x in manifest["snapshots"]],
                                   engine.XL_CORPUS_LIMIT if "xl_controls" in manifest else 3)
    engine.require(anchor in numbers and 8195 not in numbers
                   and manifest["master"] not in numbers, "invalid staged corpus")
    plan = engine.lot_plan(manifest)
    if markers[-1] == engine.DONE + digest + " " + commit:
        engine.verify_compacted_corpus(api, manifest, digest, commit, 8195,
                                       completed=True, journal_authors=authors)
        engine.require(stage.remote_head(anchor) == commit, "staging ref drift")
        engine.main_guard(api, current)
        return {"status": "READ_ONLY_DONE_VERIFIED", "p0": anchor,
                "master": manifest["master"], "main_sha": current,
                "stage_sha": commit, "manifest_sha256": digest,
                "cursor": len(plan), "applied": len(plan), "total": len(plan),
                "destruction_enabled": False}
    cursor = parse_progress(markers, digest, commit, len(plan))
    observed = {n: engine.snapshot(api, n) for n in {n for n, _, _ in plan}}
    applied = infer_prefix(manifest, observed)
    engine.require(cursor <= applied <= min(cursor + engine.LOT_BATCH, len(plan)),
                   "mutation prefix outside journal tranche")
    # Use the authoritative engine, not just the text-based inference.
    engine.verify_lot_prefix(api, manifest, digest, commit, applied, journal_count=cursor,
                             journal_authors=authors)
    engine.require(stage.remote_head(anchor) == commit, "staging ref drift")
    engine.main_guard(api, current)
    version = 1 if cursor > 0 and applied == cursor else 2
    return {"status": "READ_ONLY_PREFIX_VERIFIED", "p0": anchor,
            "master": manifest["master"], "main_sha": current,
            "stage_sha": commit, "manifest_sha256": digest,
            "cursor": cursor, "applied": applied, "total": len(plan),
            "resume_version": version, "destruction_enabled": False}


def plan_inventory(api, master, current):
    initial = engine.snapshot(api, master)
    engine.closure_identity(initial["issue"])
    body = initial["issue"]["body"] or ""
    engine.require("PU" not in {l["name"] for l in initial["issue"]["labels"]}
                   and not engine.SCOPE.findall(body), "suspended or mixed master")
    declarations = engine.MASTER_SCOPE.findall(body)
    engine.require(1 <= len(declarations) <= 100, "invalid master inventory")
    controls = [int(n) for n, _ in declarations]
    members = [int(n) for _, group in declarations for n in group.split(",")]
    engine.require(len(set(controls + members)) == len(controls + members)
                   and master not in controls + members and 8195 not in controls + members
                   and all(1 <= len(group.split(",")) <= 2 for _, group in declarations),
                   "overlapping or invalid master lots")
    lots, anchors = [], set()
    for n_text, group in declarations:
        n = int(n_text)
        snap = engine.snapshot(api, n, closed=None)
        issue = snap["issue"]
        engine.require(issue["title"] == engine.LOT_TITLE + str(master)
                       and issue["body"] == f"<!-- backlog-lot-v1: {master} ; {group} ; public -->"
                       and issue["user"]["id"] == engine.OWNER_ID
                       and not issue["labels"] and not issue["assignees"],
                       "source control drift")
        row = {"control": n, "members": [int(v) for v in group.split(",")]}
        comments = snap["comments"]
        claim = (re.fullmatch(re.escape(engine.XL_CLAIM) + r"([1-9]\d*)", comments[0]["body"])
                 if len(comments) == 1 else None)
        if claim and issue["state"] == "closed" and issue["state_reason"] == "not_planned":
            anchor = int(claim.group(1))
            engine.validate_xl_claim(snap, master, anchor, (n_text, group))
            anchors.add(anchor)
            row.update(status="claimed", anchor=anchor)
        elif issue["state"] == "open" and not comments:
            row.update(status="unclaimed_open_not_qualified")
        elif issue["state"] == "closed" and issue["state_reason"] == "completed":
            anchors.add(n)
            row.update(status="individual_closed", anchor=n)
        else:
            row.update(status="blocked_review_required")
        lots.append(row)
    hints = []
    for anchor in sorted(anchors):
        markers = journal(engine.snapshot(api, anchor), LOCAL_AUTHORS)
        # Journal hints only: no CI receipt or ref-absence proof is inferred.
        last = markers[-1] if markers else ""
        kind = next((name for prefix, name in (
            (engine.PURGED, "purged_marker_seen"), (engine.PURGE_START, "purge_started"),
            (engine.DONE, "done_marker_seen"), (engine.PROGRESS, "progress_marker_seen"),
            (engine.START, "start_marker_seen")) if last.startswith(prefix)), "no_known_marker")
        hints.append({"anchor": anchor, "journal_hint": kind})
    engine.require(engine.snapshot(api, master) == initial, "master inventory drift")
    engine.main_guard(api, current)
    return {"status": "READ_ONLY_INVENTORY", "master": master, "main_sha": current,
            "master_sha256": engine.fingerprint(initial), "lot_count": len(lots),
            "lots": lots, "anchors": hints, "destruction_enabled": False,
            "qualification_complete": False, "purge_receipts_verified": False}


def local_checkout(current):
    engine.require(engine.head() == current, "checkout is not exact main")
    engine.require(not engine.git("status", "--porcelain", "--untracked-files=normal"),
                   "checkout is dirty")


def ensure_no_closure_runs(api):
    # Reject outstanding legacy CI runs during cutover. New closure runs also
    # contend for the shared remote lease before any mutation.
    for status in ("queued", "in_progress", "waiting", "pending", "requested"):
        runs = api._request("GET", "/actions/workflows/backlog-compaction.yml/runs"
                            "?status=" + status + "&per_page=1", 200)
        engine.require(runs.get("total_count") == 0 and runs.get("workflow_runs") == [],
                       "backlog CI active or queued")


def continue_local(api, stage, anchor, master, commit, digest, current):
    """Explicitly authorized existing stage only; no new stage and no purge."""
    engine.require(anchor not in (8195, master), "invalid local anchor")
    engine.require(bool(re.fullmatch(r"[0-9a-f]{64}", digest)), "invalid manifest digest")
    engine.checked_sha(commit)
    api.authenticated_owner()
    local_checkout(current)
    engine.main_guard(api, current)
    ensure_no_closure_runs(api)
    with stage.execution_lock():
        api.write_guard = stage.assert_execution_lock
        try:
            ensure_no_closure_runs(api)
            def authority_guard():
                stage.assert_execution_lock()
                local_checkout(current)
            receipt = reconcile(api, stage, anchor, current)
            engine.require((receipt["master"], receipt["stage_sha"], receipt["manifest_sha256"])
                           == (master, commit, digest), "local authorization scope mismatch")
            if receipt["status"] == "READ_ONLY_DONE_VERIFIED":
                return {**receipt, "capitalized": False, "purge_enabled": False,
                        "executor": "local-owner"}
            raw = stage.read_remote(anchor, commit)
            engine.require(engine.sha256(raw) == digest, "staging digest mismatch")
            manifest = engine.strict_json(raw)
            cursor, applied = receipt["cursor"], receipt["applied"]
            while True:
                result = engine.apply_lot_tranche(
                    api, stage, manifest, digest, commit, cursor, applied,
                    current, authority_guard, journal_authors=LOCAL_AUTHORS,
                    writer_author=engine.OWNER_AUTHOR)
                old_cursor, cursor = cursor, result["cursor"]
                if result["status"] == "STAGED_AND_COMPACTED":
                    engine.verify_compacted_corpus(api, manifest, digest, commit, 8195,
                                                   completed=True, journal_authors=LOCAL_AUTHORS)
                    authority_guard()
                    engine.main_guard(api, current)
                    engine.require(stage.remote_head(anchor) == commit, "staging ref drift")
                    return {**result, "capitalized": False, "purge_enabled": False,
                            "executor": "local-owner", "main_sha": current}
                # Remote journal is the persistent checkpoint. Never assume that
                # an interrupted write is applied; the next invocation reconciles.
                engine.require(result["status"] == "LOT_PENDING" and cursor > old_cursor,
                               "unexpected tranche result")
                applied = cursor
        finally:
            api.write_guard = None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--master", type=int, required=True)
    recover = sub.add_parser("reconcile")
    recover.add_argument("--anchor", type=int, required=True)
    apply = sub.add_parser("continue")
    apply.add_argument("--anchor", type=int, required=True)
    apply.add_argument("--master", type=int, required=True)
    apply.add_argument("--stage-sha", required=True)
    apply.add_argument("--manifest-sha256", required=True)
    apply.add_argument("--expect-main", required=True)
    apply.add_argument("--apply", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        token = os.environ.get("GITHUB_TOKEN", "")
        api = (engine.WriteAPI if args.command == "continue" else engine.ReadOnlyAPI)(token)
        current = main_sha(api)
        if args.command == "plan":
            result = plan_inventory(api, engine.number(args.master), current)
        elif args.command == "reconcile":
            with tempfile.TemporaryDirectory(prefix="backlog-local-read-") as directory:
                stage = engine.GitStage(directory, token)
                result = reconcile(api, stage, engine.number(args.anchor), current)
        else:
            engine.require(engine.checked_sha(args.expect_main) == current, "stale local authorization")
            with tempfile.TemporaryDirectory(prefix="backlog-local-write-") as directory:
                stage = engine.GitStage(directory, token)
                result = continue_local(api, stage, engine.number(args.anchor),
                                        engine.number(args.master), args.stage_sha,
                                        args.manifest_sha256, current)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except engine.Refusal as exc:
        print("REFUSED: " + str(exc), file=sys.stderr)
        return 2
    except (KeyError, TypeError, ValueError, UnicodeError, OSError, StopIteration):
        print("REFUSED: malformed input or response", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
