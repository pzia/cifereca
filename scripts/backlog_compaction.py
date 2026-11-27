#!/usr/bin/env python3
"""P0 #8195: read-only audit and plan validation. Destruction remains disabled."""
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
DOCS = ("AGENTS.md", *(".agents/roles/" + n + ".md" for n in
        ("README", "backlog", "cicd", "core", "focus", "plugins", "ux")),
        "docs/AGENT-DELIVERY-POLICY.md", "docs/BACKLOG-COMPACTION.md")
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
REFERENCE = re.compile(r"https?://[^\s<>()\]\`]+|(?<![\w/])#\d+\b")
MAX_PLAN = 64000
MAX_PAGES = 20

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
        # Only fixed, internally generated issue endpoints. No arbitrary URL or verb.
        require(re.fullmatch(r"/issues/[1-9]\d*(?:/comments\?per_page=100&page=[1-9]\d*)?", path),
                "forbidden API path")
        req = urllib.request.Request(API + path, method="GET", headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with self.opener.open(req, timeout=30) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
            require(len(raw) <= 16 * 1024 * 1024, "API response too large")
            return strict_json(raw)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise Refusal("GitHub read failed") from None

def issue_data(api, n, closed=True):
    path = "/issues/" + str(number(n))
    issue = api.get(path)
    require(type(issue) is dict and issue.get("number") == n
            and "pull_request" not in issue, "not a repository issue")
    require(issue.get("state") == ("closed" if closed else "open"), "unexpected issue state")
    require(type(issue.get("body")) is str or issue.get("body") is None, "invalid issue body")
    return issue

def snapshot(api, n):
    first = issue_data(api, n)
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
    last = issue_data(api, n)
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
                      "created_at", "expires_at", "issues", "semantic_review"))
    require(type(plan["version"]) is int and plan["version"] == 1
            and plan["repository"] == REPO and plan["pilot_issue"] == number(pilot)
            and pilot != 8195 and plan["main_sha"] == current, "plan context mismatch")
    now = now or dt.datetime.now(dt.timezone.utc)
    created, expires = timestamp(plan["created_at"]), timestamp(plan["expires_at"])
    require(created <= now < expires <= created + dt.timedelta(hours=24), "plan expired or not current")
    require(type(plan["semantic_review"]) is str and bool(plan["semantic_review"].strip()),
            "missing semantic review")
    entries = plan["issues"]
    require(type(entries) is list and 1 <= len(entries) <= 3, "pilot limited to 1–3 issues")
    for entry in entries:
        exact_keys(entry, ("number", "snapshot_sha256", "body_after", "remove_comments", "rationale"))
    numbers = issue_numbers([entry["number"] for entry in entries])
    require(pilot not in numbers and 8195 not in numbers, "control issue excluded")
    before_docs = docs_at(plan["baseline_sha"])
    after_docs = docs_at(current)
    before, after = list(before_docs.values()), list(after_docs.values())
    snapshots = []
    for entry in entries:
        snap = snapshot(api, entry["number"])
        snapshots.append(snap)
        require(entry["snapshot_sha256"] == fingerprint(snap), "snapshot drift")
        require(type(entry["body_after"]) is str and bool(entry["body_after"].strip())
                and len(entry["body_after"].encode("utf-8")) <= MAX_PLAN
                and type(entry["rationale"]) is str and bool(entry["rationale"].strip()),
                "missing synthesis or rationale")
        ids = entry["remove_comments"]
        require(type(ids) is list and bool(ids), "empty deletion proposal")
        for value in ids:
            number(value)
        require(len(set(ids)) == len(ids) and set(ids) <= {c["id"] for c in snap["comments"]},
                "unknown or duplicate deletion target")
        before.extend(issue_texts(snap))
        after.extend([entry["body_after"], *(c["body"] for c in snap["comments"] if c["id"] not in ids)])
    refs_before = set(REFERENCE.findall("\n".join(before)))
    refs_after = set(REFERENCE.findall("\n".join(after)))
    require(refs_before <= refs_after, "explicit reference missing from retained corpus")
    # Recheck the whole bounded corpus, including its plan, before reporting.
    for entry, snap in zip(entries, snapshots):
        require(fingerprint(snapshot(api, entry["number"])) == fingerprint(snap), "snapshot drift")
    require((issue_data(api, pilot, closed=False)["body"] or "").encode("utf-8") == raw,
            "pilot plan changed")
    old, new = metrics(before), metrics(after + [raw.decode("utf-8")])
    # Count the entire retained plan as overhead; journal and actual effects are not measured yet.
    return {"status": "STRUCTURAL_VALIDATION_ONLY", "plan_sha256": expected,
            "main_sha": current, "issues": numbers, "before": old, "projected_after_with_plan": new,
            "projected_gain": {k: old[k] - new[k] for k in old},
            "journal_included": False, "semantic_equivalence_proven": False,
            "destruction_enabled": False}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("audit", "validate", "apply"))
    parser.add_argument("--issues", default="")
    parser.add_argument("--pilot", type=int)
    parser.add_argument("--plan-sha256")
    args = parser.parse_args(argv)
    try:
        require(args.mode != "apply", "destruction disabled: independent owner consent not established")
        current = head()
        api = ReadOnlyAPI(os.environ.get("GITHUB_TOKEN", ""))
        if args.mode == "audit":
            require(bool(re.fullmatch(r"[1-9]\d*(?:,[1-9]\d*){0,2}", args.issues)), "invalid audit list")
            numbers = issue_numbers([int(n) for n in args.issues.split(",")])
            result = {"status": "READ_ONLY_AUDIT", "main_sha": current,
                      "issues": [audit_issue(snapshot(api, n)) for n in numbers],
                      "documents": doc_report(doc_texts(current)), "destruction_enabled": False}
        else:
            pilot = number(args.pilot)
            raw = (issue_data(api, pilot, closed=False)["body"] or "").encode("utf-8")
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
