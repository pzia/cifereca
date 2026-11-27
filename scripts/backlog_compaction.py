#!/usr/bin/env python3
"""P0 #8195: bounded audit, validation and one-shot plan execution."""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter

REPO = "pzia/cifereca"
API = "https://api.github.com/repos/" + REPO
BASELINE = "8921002d1a5b9e0a4a0f0f592dbb6d669a283f4c"
DOCS = ("AGENTS.md", *(".agents/roles/" + n + ".md" for n in
        ("README", "backlog", "cicd", "core", "focus", "plugins", "ux")),
        "docs/AGENT-DELIVERY-POLICY.md", "docs/CORE-P1-3913-DIAGNOSTIC-TOOLS.md",
        "docs/BACKLOG-COMPACTION.md")
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
REFERENCE = re.compile(r"https?://[^\s<>()\]\`]+|(?<![\w/])#\d+\b")
COMMENT_REF = re.compile(r"(?:issuecomment-|/issues/comments/)([0-9]+)\b")
MAX_PLAN = 64000
MAX_PAGES = 20
MAX_REMOVALS = 10
CONSUMED = "BACKLOG-COMPACTION-CONSUMED v1 "

class Refusal(Exception):
    """Fixed diagnostic only; never include issue or plan contents."""

def require(condition, reason):
    if not condition:
        raise Refusal(reason)

def sha256(data):
    return hashlib.sha256(data).hexdigest()

def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")

def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: require(False, "invalid JSON constant"))
    except (ValueError, UnicodeError, RecursionError):
        raise Refusal("invalid JSON") from None

def exact_keys(obj, keys):
    require(type(obj) is dict and set(obj) == set(keys), "unknown or missing plan field")

def number(value):
    require(type(value) is int and 0 < value < 2**53, "invalid issue/comment id")
    return value

def issue_numbers(values):
    require(type(values) is list and 1 <= len(values) <= 3, "pilot limited to 1–3 issues")
    for value in values:
        number(value)
    require(len(set(values)) == len(values), "duplicate issue")
    return values

def metrics(texts):
    chars = sum(len(t) for t in texts)
    size = sum(len(t.encode("utf-8")) for t in texts)
    return {"characters": chars, "bytes": size, "tokens_estimated": (size + 3) // 4}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Refusal("unexpected API redirect")

class ReadOnlyAPI:
    def __init__(self, token):
        require(bool(token), "missing read token")
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())

    def get(self, path):
        require(path == "/git/ref/heads/main" or re.fullmatch(
            r"/issues/[1-9]\d*(?:/comments\?per_page=100&page=[1-9]\d*)?", path),
            "forbidden API path")
        return self._request("GET", path, 200)

    def _request(self, method, path, status, payload=None):
        req = urllib.request.Request(API + path, method=method,
            data=None if payload is None else encode(payload), headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with self.opener.open(req, timeout=30) as response:
                require(response.status == status, "unexpected API status")
                raw = response.read(16 * 1024 * 1024 + 1)
            require(len(raw) <= 16 * 1024 * 1024, "API response too large")
            if status == 204:
                require(not raw, "unexpected delete response")
                return None
            return strict_json(raw)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise Refusal("GitHub request failed; do not retry mutations") from None

class WriteAPI(ReadOnlyAPI):
    """Fixed mutation endpoints; callers must pass the validated plan guards."""
    def consume(self, pilot, marker):
        require(marker.startswith(CONSUMED), "invalid consumption marker")
        return self._request("POST", f"/issues/{number(pilot)}/comments", 201, {"body": marker})

    def replace_body(self, n, body):
        return self._request("PATCH", f"/issues/{number(n)}", 200, {"body": body})

    def delete_comment(self, comment_id):
        return self._request("DELETE", f"/issues/comments/{number(comment_id)}", 204)

def issue_data(api, n, closed=True):
    path = "/issues/" + str(number(n))
    issue = api.get(path)
    require(type(issue) is dict and issue.get("number") == n
            and "pull_request" not in issue, "not a repository issue")
    states = ("open", "closed") if closed is None else (("closed",) if closed else ("open",))
    require(issue.get("state") in states, "unexpected issue state")
    require(type(issue.get("body")) is str or issue.get("body") is None, "invalid issue body")
    return issue

def snapshot(api, n, closed=True):
    first = issue_data(api, n, closed=closed)
    comments = []
    for page in range(1, MAX_PAGES + 1):
        batch = api.get("/issues/%d/comments?per_page=100&page=%d" % (n, page))
        require(type(batch) is list and len(batch) <= 100, "invalid comments page")
        comments.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise Refusal("pagination limit reached")
    ids = []
    for c in comments:
        require(type(c) is dict and type(c.get("body")) is str, "invalid comment")
        ids.append(number(c.get("id")))
    require(len(set(ids)) == len(ids), "duplicate comment during pagination")
    last = issue_data(api, n, closed=closed)
    require(first == last and len(comments) == first.get("comments"), "issue changed during scan")
    # Hash entire REST snapshots: fail conservatively on metadata or content drift.
    return {"issue": first, "comments": sorted(comments, key=lambda c: c["id"])}

def fingerprint(snap):
    return sha256(encode(snap))

def issue_texts(snap):
    return [snap["issue"]["body"] or "", *(c["body"] for c in snap["comments"])]

def audit_issue(snap):
    return {"number": snap["issue"]["number"], "snapshot_sha256": fingerprint(snap),
            "metrics": metrics(issue_texts(snap)),
            "body_sha256": sha256((snap["issue"]["body"] or "").encode("utf-8")),
            "comments": [{"id": c["id"], "body_sha256": sha256(c["body"].encode("utf-8")),
                          "metrics": metrics([c["body"]])} for c in snap["comments"]]}

def git(*args):
    try:
        return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        raise Refusal("Git read failed") from None

def head():
    ref = git("rev-parse", "HEAD").decode("ascii").strip()
    require(bool(SHA.fullmatch(ref)), "invalid checkout HEAD")
    return ref

def doc_texts(ref):
    require(type(ref) is str and bool(SHA.fullmatch(ref)), "invalid document SHA")
    result = {}
    for path in DOCS:
        # Only the new runbook may be absent from the pre-project baseline.
        if path == DOCS[-1] and not git("ls-tree", ref, "--", path):
            result[path] = ""
        else:
            result[path] = git("show", ref + ":" + path).decode("utf-8")
    return result

def doc_report(docs):
    duplicates = Counter()
    for body in docs.values():
        duplicates.update(set(p.strip() for p in re.split(r"\n\s*\n", body)
                              if len(p.strip()) >= 100))
    return {"metrics": metrics(list(docs.values())),
            "files": {p: {"sha256": sha256(t.encode("utf-8")), "metrics": metrics([t])}
                      for p, t in docs.items()},
            "duplicate_paragraph_candidates": [
                {"sha256": sha256(p.encode("utf-8")), "copies": n, "characters": len(p)}
                for p, n in sorted(duplicates.items()) if n > 1]}

def timestamp(value):
    require(type(value) is str and bool(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value)),
            "invalid UTC time")
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise Refusal("invalid UTC time") from None

def validate_plan(raw, expected, pilot, current, api, docs_at=doc_texts, now=None):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_PLAN, "invalid plan size")
    require(type(expected) is str and bool(DIGEST.fullmatch(expected))
            and sha256(raw) == expected, "plan digest mismatch")
    plan = strict_json(raw)
    exact_keys(plan, ("version", "repository", "pilot_issue", "main_sha", "baseline_sha",
                      "created_at", "expires_at", "closure_p0", "issues", "retained_issues", "semantic_review",
                      "execution_review"))
    require(type(plan["version"]) is int and plan["version"] == 2
            and plan["repository"] == REPO and number(plan["pilot_issue"]) == number(pilot)
            and pilot != 8195 and plan["main_sha"] == current
            and plan["baseline_sha"] == current,
            "plan context mismatch")
    closure = plan["closure_p0"]
    if closure is not None:
        number(closure)
    pilot_before = snapshot(api, pilot, closed=False)
    pilot_fingerprint = fingerprint(pilot_before)
    require(not any(c["body"].startswith(CONSUMED) for c in pilot_before["comments"]),
            "pilot already consumed")
    require((pilot_before["issue"]["body"] or "").encode("utf-8") == raw, "pilot plan changed")
    now = now or dt.datetime.now(dt.timezone.utc)
    created, expires = timestamp(plan["created_at"]), timestamp(plan["expires_at"])
    require(created <= now < expires <= created + dt.timedelta(hours=24), "plan expired or not current")
    require(type(plan["semantic_review"]) is str and bool(plan["semantic_review"].strip()),
            "missing semantic review")
    exact_keys(plan["execution_review"], ("corpus_quiescent", "known_inbound_references", "global_gain"))
    require(all(v is True for v in plan["execution_review"].values()), "missing Backlog execution review")
    entries = plan["issues"]
    require(type(entries) is list and 1 <= len(entries) <= 3, "pilot limited to 1–3 issues")
    for entry in entries:
        exact_keys(entry, ("number", "snapshot_sha256", "body_after", "remove_comments", "rationale"))
    numbers = issue_numbers([entry["number"] for entry in entries])
    require(pilot not in numbers and 8195 not in numbers, "control issue excluded")
    before_docs = docs_at(plan["baseline_sha"])
    after_docs = docs_at(current)
    before, after = list(before_docs.values()), list(after_docs.values())
    retained = plan["retained_issues"]
    require(type(retained) is list and len(retained) <= 3, "too many retained issues")
    for entry in retained:
        exact_keys(entry, ("number", "snapshot_sha256"))
    retained_numbers = [number(entry["number"]) for entry in retained]
    require(len(set(retained_numbers)) == len(retained_numbers)
            and not set(retained_numbers) & set(numbers + [pilot, 8195]), "overlapping retained issue")
    if closure is not None:
        require(closure in numbers + retained_numbers, "closure P0 outside bounded corpus")

    def check_closure(snap):
        issue = snap["issue"]
        if issue["number"] == closure:
            require(issue["state"] == "closed" and {label["name"] for label in issue["labels"]}
                    & {"P0", "P-1", "P1", "P2", "P3"} == {"P0"}, "closure parent must be closed P0")

    for entry in retained:
        snap = snapshot(api, entry["number"], closed=None)
        check_closure(snap)
        require(entry["snapshot_sha256"] == fingerprint(snap), "retained issue drift")
        before.extend(issue_texts(snap))
        after.extend(issue_texts(snap))
    removed_ids = set()
    for entry in entries:
        snap = snapshot(api, entry["number"])
        check_closure(snap)
        require(entry["snapshot_sha256"] == fingerprint(snap), "snapshot drift")
        require(type(entry["body_after"]) is str and bool(entry["body_after"].strip())
                and len(entry["body_after"].encode("utf-8")) <= MAX_PLAN
                and type(entry["rationale"]) is str and bool(entry["rationale"].strip()),
                "missing synthesis or rationale")
        ids = entry["remove_comments"]
        require(type(ids) is list, "invalid deletion list")
        require(bool(ids) or entry["body_after"] != (snap["issue"]["body"] or ""), "empty change proposal")
        for value in ids:
            number(value)
        require(len(set(ids)) == len(ids) and set(ids) <= {c["id"] for c in snap["comments"]},
                "unknown or duplicate deletion target")
        removed_ids.update(ids)
        before.extend(issue_texts(snap))
        after.extend([entry["body_after"], *(c["body"] for c in snap["comments"] if c["id"] not in ids)])
    require(len(removed_ids) <= MAX_REMOVALS, "too many comment removals")
    refs_before = set(REFERENCE.findall("\n".join(before)))
    refs_after = set(REFERENCE.findall("\n".join(after)))
    require(refs_before <= refs_after, "explicit reference missing from retained corpus")
    cited_ids = {int(n) for n in COMMENT_REF.findall("\n".join(before + after + issue_texts(pilot_before)))}
    require(not removed_ids & cited_ids, "known reference to deleted comment")
    # Recheck the whole bounded corpus, including its plan, before reporting.
    for entry in entries:
        require(fingerprint(snapshot(api, entry["number"])) == entry["snapshot_sha256"], "snapshot drift")
    for entry in retained:
        require(fingerprint(snapshot(api, entry["number"], closed=None)) == entry["snapshot_sha256"], "retained issue drift")
    require(fingerprint(snapshot(api, pilot, closed=False)) == pilot_fingerprint, "pilot plan changed")
    old, new = metrics(before), metrics(after + issue_texts(pilot_before))
    # Count the entire pilot issue as overhead; document savings are measured separately
    # at closure and never recredited to each execution.
    return {"status": "STRUCTURAL_VALIDATION_ONLY", "plan_sha256": expected,
            "main_sha": current, "closure_p0": closure, "issues": numbers, "retained_issues": retained_numbers,
            "pilot_snapshot_sha256": pilot_fingerprint,
            "before": old, "projected_after_with_plan": new,
            "projected_gain": {k: old[k] - new[k] for k in old},
            "journal_included": False, "semantic_equivalence_proven": False,
            "destruction_enabled": False}

def apply_context(env, current):
    require(all(env.get(k) == v for k, v in {
        "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": REPO, "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": current, "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_ACTOR": "pzia", "GITHUB_ACTOR_ID": "11092036",
        "GITHUB_TRIGGERING_ACTOR": "pzia",
        "GITHUB_WORKFLOW_REF": REPO + "/.github/workflows/backlog-compaction.yml@refs/heads/main",
    }.items()), "apply requires first manual pzia run on main")
    run = env.get("GITHUB_RUN_ID", "")
    require(bool(re.fullmatch(r"[1-9]\d{0,19}", run)), "invalid run id")
    return run


def expected_change(before, after, *, body=None, remove=None, marker=None):
    """Only permit our expected body/comment mutation and server timestamps."""
    wanted = dict(before["issue"])
    observed = dict(after["issue"])
    wanted.pop("updated_at", None)
    observed.pop("updated_at", None)
    comments = list(before["comments"])
    if body is not None:
        wanted["body"] = body
    if remove is not None:
        comments = [c for c in comments if c["id"] != remove]
        wanted["comments"] -= 1
    if marker is not None:
        matches = [c for c in after["comments"] if c["id"] == marker["id"]]
        require(len(matches) == 1 and matches[0]["body"] == marker["body"]
                and all(c["id"] != marker["id"] for c in comments), "consumption marker not verified")
        comments.append(matches[0])
        wanted["comments"] += 1
    require(wanted == observed and sorted(comments, key=lambda c: c["id"]) == after["comments"],
            "unexpected post-mutation state; stop")


def apply_plan(raw, expected, pilot, current, api, run, docs_at=doc_texts, clock=None):
    clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
    report = validate_plan(raw, expected, pilot, current, api, docs_at=docs_at, now=clock())
    plan = strict_json(raw)
    entries = plan["issues"]
    targets = {e["number"]: e["snapshot_sha256"] for e in entries}
    retained = {e["number"]: e["snapshot_sha256"] for e in plan["retained_issues"]}
    expected_states = {**targets, **retained, pilot: report["pilot_snapshot_sha256"]}
    marker_body = CONSUMED + expected + " run=" + run
    marker_metrics = metrics([marker_body])
    require(all(report["projected_gain"][k] > marker_metrics[k] for k in ("characters", "bytes")),
            "no positive bounded projected gain including consumption journal")

    def guard():
        require(clock() < timestamp(plan["expires_at"]), "plan expired during execution")
        ref = api.get("/git/ref/heads/main")
        require(ref.get("ref") == "refs/heads/main" and ref.get("object", {}).get("sha") == current,
                "main changed during execution")
        states = {n: snapshot(api, n, closed=False if n == pilot else None if n in retained else True)
                  for n in expected_states}
        require(all(fingerprint(states[n]) == digest for n, digest in expected_states.items()),
                "corpus changed during execution")
        require(clock() < timestamp(plan["expires_at"]), "plan expired during execution")
        ref = api.get("/git/ref/heads/main")
        require(ref.get("ref") == "refs/heads/main" and ref.get("object", {}).get("sha") == current,
                "main changed during execution")
        return states

    def verify(n, before, **change):
        after = snapshot(api, n, closed=False if n == pilot else True)
        expected_change(before, after, **change)
        expected_states[n] = fingerprint(after)

    # Persistent consumption precedes every target mutation. Never retry a failed request.
    states = guard()
    marker = api.consume(pilot, marker_body)
    require(type(marker) is dict and marker.get("body") == marker_body, "invalid consumption response")
    number(marker.get("id"))
    verify(pilot, states[pilot], marker=marker)

    # First write and verify every synthesis; only then delete any comment.
    for e in entries:
        states = guard()
        n = e["number"]
        if states[n]["issue"]["body"] != e["body_after"]:
            api.replace_body(n, e["body_after"])
            verify(n, states[n], body=e["body_after"])
    for e in entries:
        n = e["number"]
        for comment_id in e["remove_comments"]:
            states = guard()
            require(states[n]["issue"]["body"] == e["body_after"], "synthesis not verified")
            api.delete_comment(comment_id)
            verify(n, states[n], remove=comment_id)
    states = guard()
    after = metrics(list(docs_at(current).values()) + [t for snap in states.values() for t in issue_texts(snap)])
    report.update(status="APPLIED_BOUNDED_PLAN", actual_after_with_plan=after,
                  actual_bounded_gain={k: report["before"][k] - after[k] for k in after},
                  journal_included=True, external_control_overhead_included=False,
                  destruction_enabled=True, run_id=run)
    return report

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("audit", "validate", "apply"))
    parser.add_argument("--issues", default="")
    parser.add_argument("--pilot", type=int)
    parser.add_argument("--plan-sha256")
    args = parser.parse_args(argv)
    try:
        if args.mode == "apply":
            # Refuse local invocations before Git or network access.
            run = apply_context(os.environ, os.environ.get("GITHUB_SHA", ""))
        current = head()
        if args.mode == "apply":
            apply_context(os.environ, current)
        api = (WriteAPI if args.mode == "apply" else ReadOnlyAPI)(os.environ.get("GITHUB_TOKEN", ""))
        if args.mode == "audit":
            require(bool(re.fullmatch(r"[1-9]\d*(?:,[1-9]\d*){0,2}", args.issues)), "invalid audit list")
            numbers = issue_numbers([int(n) for n in args.issues.split(",")])
            result = {"status": "READ_ONLY_AUDIT", "main_sha": current,
                      "issues": [audit_issue(snapshot(api, n)) for n in numbers],
                      "documents": doc_report(doc_texts(current)), "destruction_enabled": False}
        else:
            pilot = number(args.pilot)
            raw = (issue_data(api, pilot, closed=False)["body"] or "").encode("utf-8")
            if args.mode == "apply":
                result = apply_plan(raw, args.plan_sha256, pilot, current, api, run)
            else:
                result = validate_plan(raw, args.plan_sha256, pilot, current, api)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Refusal as exc:
        print("REFUSED: " + str(exc), file=sys.stderr)
        return 2
    except (KeyError, TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        print("REFUSED: malformed input or response", file=sys.stderr)
        return 2

if __name__ == "__main__":
    sys.exit(main())
