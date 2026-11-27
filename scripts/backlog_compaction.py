#!/usr/bin/env python3
"""P0 #8195: verified staging, contextual compaction and deferred capitalization."""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
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
REFERENCE = re.compile(r"https?://[^\s<>()\]\`]+|(?<![\w/])#\d+\b")
MAX_PAGES = 20
MAX_REMOVALS = 10

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
            r"/issues/[1-9]\d*(?:/(?:comments|sub_issues)\?per_page=100&page=[1-9]\d*)?", path),
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
    """Fixed issue mutation endpoints; staging must be verified first."""
    def mark(self, pilot, marker):
        require(marker.startswith(("BACKLOG-STAGING-START v1 ", "BACKLOG-STAGING-DONE v1 ", "BACKLOG-STAGING-PURGED v1 ")), "invalid staging marker")
        return self._request("POST", f"/issues/{number(pilot)}/comments", 201, {"body": marker})

    def replace_body(self, n, body):
        return self._request("PATCH", f"/issues/{number(n)}", 200, {"body": body})

    def replace_comment(self, comment_id, body):
        return self._request("PATCH", f"/issues/comments/{number(comment_id)}", 200, {"body": body})

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

def expected_change(before, after, *, body=None, comment=None, marker=None):
    """Only permit our expected body/comment mutation and server timestamps."""
    wanted = dict(before["issue"])
    observed = dict(after["issue"])
    wanted.pop("updated_at", None)
    observed.pop("updated_at", None)
    comments = [dict(c) for c in before["comments"]]
    actual = [dict(c) for c in after["comments"]]
    if body is not None:
        wanted["body"] = body
    if comment is not None:
        cid, replacement = comment
        target = next(c for c in comments if c["id"] == cid)
        target["body"] = replacement
        target.pop("updated_at", None)
        next(c for c in actual if c["id"] == cid).pop("updated_at", None)
    if marker is not None:
        matches = [c for c in after["comments"] if c["id"] == marker["id"]]
        require(len(matches) == 1 and matches[0]["body"] == marker["body"]
                and all(c["id"] != marker["id"] for c in comments), "consumption marker not verified")
        comments.append(matches[0])
        wanted["comments"] += 1
    require(wanted == observed and sorted(comments, key=lambda c: c["id"]) == actual,
            "unexpected post-mutation state; stop")


STAGE_LIMIT = 1024 * 1024
OWNER_ID = 11092036
PRIORITIES = {"P0", "P-1", "P1", "P2", "P3"}
SCOPE = re.compile(r"(?m)^<!-- backlog-staging-v1: (none|[1-9]\d*(?:,[1-9]\d*)*) ; public -->$")

def closure_event(env):
    require(all(env.get(k) == v for k, v in {
        "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "issues",
        "GITHUB_REPOSITORY": REPO, "GITHUB_REF": "refs/heads/main",
        "GITHUB_RUN_ATTEMPT": "1", "GITHUB_ACTOR": "pzia",
        "GITHUB_ACTOR_ID": str(OWNER_ID), "GITHUB_TRIGGERING_ACTOR": "pzia",
        "GITHUB_WORKFLOW_REF": REPO + "/.github/workflows/backlog-compaction.yml@refs/heads/main",
    }.items()), "invalid P0 closure context")
    try:
        with open(env.get("GITHUB_EVENT_PATH", ""), "rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
    except OSError:
        raise Refusal("cannot read closure event") from None
    require(len(raw) <= 1024 * 1024, "closure event too large")
    event = strict_json(raw)
    require(event["action"] == "closed" and event["repository"]["full_name"] == REPO
            and event["sender"]["id"] == OWNER_ID and event["sender"]["login"] == "pzia",
            "unauthorized P0 closure")
    issue = event["issue"]
    closed = timestamp(issue["closed_at"])
    now = dt.datetime.now(dt.timezone.utc)
    require(closed <= now < closed + dt.timedelta(hours=24), "closure expired")
    return issue

def closure_identity(issue):
    require("pull_request" not in issue and issue["state"] == "closed"
            and issue["state_reason"] == "completed"
            and {l["name"] for l in issue["labels"]} & PRIORITIES == {"P0"},
            "not a completed closed P0")
    timestamp(issue["closed_at"])
    return {k: issue[k] for k in ("id", "number", "title", "body", "state",
                                  "state_reason", "closed_at", "labels")}

def public_projection(snap):
    """Public text and provenance, not a raw REST dump."""
    issue = snap["issue"]
    def author(obj):
        user = obj.get("user")
        require(type(user) is dict and type(user.get("login")) is str, "unknown author")
        return {"login": user["login"], "id": number(user["id"])}
    result = {"issue": {k: issue[k] for k in ("id", "number", "title", "body",
              "state", "state_reason", "created_at", "updated_at", "closed_at", "html_url")},
              "comments": []}
    result["issue"]["author"] = author(issue)
    result["issue"]["labels"] = sorted(label["name"] for label in issue["labels"])
    for c in snap["comments"]:
        result["comments"].append({**{k: c[k] for k in
            ("id", "body", "html_url", "created_at", "updated_at")}, "author": author(c)})
    # A heuristic is not a DLP guarantee. Owner's public declaration is mandatory.
    secret = re.compile(r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|Bearer\s+[A-Za-z0-9._~+/-]{20,})")
    require(not any(secret.search(x) for x in issue_texts(snap)), "possible secret; staging refused")
    return result

def collect_closure(api, event_issue, current):
    require(bool(SHA.fullmatch(current)), "invalid main SHA")
    identity = closure_identity(event_issue)
    root = number(identity["number"])
    require(root != 8195, "infrastructure issue excluded")
    require("PU" not in {l["name"] for l in identity["labels"]}, "suspended P0")
    matches = SCOPE.findall(identity["body"] or "")
    require(len(matches) == 1, "missing or ambiguous public closure scope")
    children = [] if matches[0] == "none" else [int(n) for n in matches[0].split(",")]
    numbers = issue_numbers([root, *children])
    require(8195 not in numbers, "infrastructure issue excluded")
    snapshots = {n: snapshot(api, n) for n in numbers}
    require(closure_identity(snapshots[root]["issue"]) == identity, "closure event drift")
    edges = {}
    for n, snap in snapshots.items():
        require(snap["issue"]["state_reason"] == "completed", "non-completed corpus member")
        require("PU" not in {l["name"] for l in snap["issue"]["labels"]}, "suspended corpus member")
        require(not any(c["body"].startswith("BACKLOG-STAGING-") for c in snap["comments"]), "corpus already consumed")
        if n != root:
            require({l["name"] for l in snap["issue"]["labels"]} & PRIORITIES == {"P-1"},
                    "child must be P-1")
        subs = api.get(f"/issues/{n}/sub_issues?per_page=100&page=1")
        require(type(subs) is list and len(subs) < 100, "ambiguous native sub-issues")
        ids = sorted(number(child["number"]) for child in subs)
        summary = snap["issue"].get("sub_issues_summary")
        require(type(summary) is dict and summary.get("total") == len(ids)
                and set(ids) <= set(children) - {n}, "unresolved native sub-issue")
        edges[n] = ids
    for n in children:
        require(any(n in ids for ids in edges.values()) or
                ("#" + str(root)) in set(REFERENCE.findall(snapshots[n]["issue"]["body"] or "")),
                "child relation unverified")
    for n, snap in snapshots.items():
        require(snapshot(api, n) == snap, "corpus drift")
        subs = api.get(f"/issues/{n}/sub_issues?per_page=100&page=1")
        require(sorted(number(child["number"]) for child in subs) == edges[n], "scope drift")
    ref = api.get("/git/ref/heads/main")
    require(ref.get("ref") == "refs/heads/main" and ref.get("object", {}).get("sha") == current,
            "main changed during collection")
    manifest = {"version": 1, "repository": REPO, "p0": root, "main_sha": current,
                "edges": {str(n): edges[n] for n in sorted(edges)},
                "snapshots": [public_projection(snapshots[n]) for n in sorted(snapshots)]}
    require(len(encode(manifest)) <= STAGE_LIMIT, "staging corpus too large")
    return manifest

STAGE_PREFIX = "refs/heads/staging/backlog/p0-"
START = "BACKLOG-STAGING-START v1 "
DONE = "BACKLOG-STAGING-DONE v1 "
PURGED = "BACKLOG-STAGING-PURGED v1 "
PURGE_TITLE = "Capitalisation #8195 — "

def stage_ref(n):
    return STAGE_PREFIX + str(number(n))

def checked_sha(value):
    require(type(value) is str and bool(SHA.fullmatch(value)), "invalid Git SHA")
    return value

class GitStage:
    """Isolated bare repo, fixed remote, no credentials in argv, leases on both writes."""
    def __init__(self, directory, token=None):
        self.directory = directory
        self.remote = "https://github.com/" + REPO + ".git"
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": directory,
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
                    "GIT_AUTHOR_NAME": "Backlog staging", "GIT_AUTHOR_EMAIL": "staging@invalid",
                    "GIT_COMMITTER_NAME": "Backlog staging", "GIT_COMMITTER_EMAIL": "staging@invalid"}
        if token:
            auth = base64.b64encode(("x-access-token:" + token).encode()).decode("ascii")
            self.env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                            GIT_CONFIG_VALUE_0="Authorization: Basic " + auth)
        self.run("init", "--bare", "--quiet")

    def run(self, *args, data=None):
        try:
            return subprocess.run(["git", "-C", self.directory, *args], input=data,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
                env=self.env, timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            raise Refusal("Git staging operation failed; do not retry mutations") from None

    def remote_head(self, n):
        ref = stage_ref(n)
        lines = self.run("ls-remote", "--refs", self.remote, ref).splitlines()
        if not lines:
            return None
        require(len(lines) == 1, "ambiguous staging ref")
        digest, name = lines[0].decode("ascii").split("\t")
        require(name == ref, "wrong staging ref")
        return checked_sha(digest)

    def make_commit(self, raw):
        require(0 < len(raw) <= STAGE_LIMIT, "staging corpus too large")
        blob = self.run("hash-object", "-w", "--stdin", data=raw).strip()
        tree = self.run("mktree", data=b"100644 blob " + blob + b"\tmanifest.json\n").strip()
        return checked_sha(self.run("commit-tree", tree.decode("ascii"),
                           data=b"Temporary P0 staging\n").decode("ascii").strip())

    def read_commit(self, commit):
        checked_sha(commit)
        require(self.run("rev-list", "--count", commit).strip() == b"1", "staging has ancestry")
        require(self.run("ls-tree", "--name-only", commit).strip() == b"manifest.json",
                "unexpected staging tree")
        raw = self.run("show", commit + ":manifest.json")
        require(0 < len(raw) <= STAGE_LIMIT, "invalid staged size")
        return raw

    def read_remote(self, n, commit):
        require(self.remote_head(n) == checked_sha(commit), "staging ref drift")
        self.run("fetch", "--no-tags", self.remote, stage_ref(n))
        require(self.run("rev-parse", "FETCH_HEAD").decode().strip() == commit, "staging fetch drift")
        require(self.remote_head(n) == commit, "staging ref drift")
        return self.read_commit(commit)

    def publish(self, n, raw):
        require(self.remote_head(n) is None, "staging already exists")
        commit = self.make_commit(raw)
        ref = stage_ref(n)
        self.run("push", "--force-with-lease=" + ref + ":", self.remote, commit + ":" + ref)
        # Fetch in a distinct empty object store: verification cannot use local cached content.
        with tempfile.TemporaryDirectory(prefix="backlog-verify-") as directory:
            verifier = GitStage(directory)
            verifier.remote = self.remote
            require(verifier.read_remote(n, commit) == raw, "remote staging verification failed")
        return commit

    def purge(self, n, commit):
        require(self.remote_head(n) == checked_sha(commit), "staging ref drift")
        ref = stage_ref(n)
        self.run("push", "--force-with-lease=" + ref + ":" + commit, self.remote, ":" + ref)
        require(self.remote_head(n) is None, "staging ref still present")

def local_stage_roundtrip(manifest):
    raw = encode(manifest)
    with tempfile.TemporaryDirectory(prefix="backlog-stage-") as directory:
        stage = GitStage(directory)
        commit = stage.make_commit(raw)
        ref = stage_ref(manifest["p0"])
        stage.run("update-ref", ref, commit, "0" * 40)
        require(stage.read_commit(commit) == raw, "staging content mismatch")
        stage.run("update-ref", "-d", ref, commit)
        require(not stage.run("for-each-ref"), "staging ref not purged")
    return {"status": "STAGING_LOCAL_ROUNDTRIP_ONLY", "p0": manifest["p0"],
            "manifest_sha256": sha256(raw), "bytes": len(raw), "destruction_enabled": False}

def main_guard(api, current):
    ref = api.get("/git/ref/heads/main")
    require(ref.get("ref") == "refs/heads/main" and ref.get("object", {}).get("sha") == current,
            "main changed")

def compact_text(text, parent):
    if not text:
        return text
    refs = sorted(set(REFERENCE.findall(text)))
    stub = "Corpus transféré — P0 #" + str(parent) + " (consolidation #8195)."
    if refs:
        stub += "\nRéférences : " + " ".join(refs)
    # Both metrics must decrease. Keep short comments and their unique links intact.
    return stub if len(stub) < len(text) and len(stub.encode()) < len(text.encode()) else text

def consolidate(api, stage, event_issue, current, clock=None):
    clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
    manifest = collect_closure(api, event_issue, current)
    parent = manifest["p0"]
    raw = encode(manifest)
    digest = sha256(raw)
    require(stage.remote_head(parent) is None, "staging already exists")
    states = {s["issue"]["number"]: snapshot(api, s["issue"]["number"]) for s in manifest["snapshots"]}
    require([public_projection(states[n]) for n in sorted(states)] == manifest["snapshots"],
            "corpus changed before staging")
    require(not any(c["body"].startswith("BACKLOG-STAGING-") for snap in states.values()
                    for c in snap["comments"]), "corpus already consumed")
    mutations, projected = [], []
    for n, snap in states.items():
        old = snap["issue"]["body"]
        new = compact_text(old, parent)
        if old != new:
            mutations.append((n, None, new))
        projected.append(new or "")
        for c in snap["comments"]:
            new = compact_text(c["body"], parent)
            if new != c["body"]:
                mutations.append((n, c["id"], new))
            projected.append(new)
    require(sum(cid is not None for _, cid, _ in mutations) <= MAX_REMOVALS, "pilot comment limit exceeded")
    before = metrics([t for snap in states.values() for t in issue_texts(snap)])
    # Count both persistent journals, even before the commit SHA is known.
    estimate = metrics(projected + [START + digest + " " + "0" * 40, DONE + digest + " " + "0" * 40])
    require(all(before[k] > estimate[k] for k in ("characters", "bytes")), "no positive contextual gain")
    commit = None
    def guard():
        require(clock() < timestamp(event_issue["closed_at"]) + dt.timedelta(hours=24), "closure expired")
        main_guard(api, current)
        require(all(snapshot(api, n) == snap for n, snap in states.items()), "corpus drift")
        for n in states:
            subs = api.get(f"/issues/{n}/sub_issues?per_page=100&page=1")
            require(sorted(number(child["number"]) for child in subs) == manifest["edges"][str(n)], "scope drift")
        require(stage.remote_head(parent) == commit, "staging ref drift")
        main_guard(api, current)
    guard()
    commit = stage.publish(parent, raw)
    require(stage.read_remote(parent, commit) == raw, "staging mismatch")
    guard()
    def mark(text):
        marker = api.mark(parent, text)
        require(marker.get("body") == text, "unexpected journal")
        after = snapshot(api, parent)
        expected_change(states[parent], after, marker=marker)
        states[parent] = after
    mark(START + digest + " " + commit)
    for n, cid, body in mutations:
        guard()
        if cid is None:
            api.replace_body(n, body)
            change = {"body": body}
        else:
            api.replace_comment(cid, body)
            change = {"comment": (cid, body)}
        after = snapshot(api, n)
        expected_change(states[n], after, **change)
        states[n] = after
    guard()
    mark(DONE + digest + " " + commit)
    guard()
    after = metrics([t for snap in states.values() for t in issue_texts(snap)])
    return {"status": "STAGED_AND_COMPACTED", "p0": parent, "stage_sha": commit,
            "manifest_sha256": digest, "staging_bytes": len(raw), "before": before, "after": after,
            "context_gain": {k: before[k] - after[k] for k in before}, "capitalized": False,
            "global_gain_proven": False}

def canonical_evidence(ref, path):
    checked_sha(ref)
    require(type(path) is str and (path == "AGENTS.md" or
            bool(re.fullmatch(r"(?:docs|\.agents/roles|\.agents/skills)/[A-Za-z0-9_./-]+\.md", path)))
            and all(part not in ("", ".", "..") for part in path.split("/")), "invalid evidence path")
    return git("show", ref + ":" + path)

def purge_capitalized(api, stage, event_issue, current, evidence_at=canonical_evidence):
    require(event_issue["state"] == "closed" and event_issue["state_reason"] == "completed"
            and event_issue["title"].startswith(PURGE_TITLE) and not event_issue["labels"]
            and not event_issue["assignees"] and event_issue["user"]["id"] == OWNER_ID,
            "invalid capitalization control")
    control = number(event_issue["number"])
    control_before = snapshot(api, control)
    require(all(control_before["issue"].get(k) == event_issue.get(k) for k in
                ("id", "number", "body", "title", "state", "state_reason", "closed_at", "labels", "assignees")),
            "capitalization control changed")
    data = strict_json(event_issue["body"].encode())
    exact_keys(data, ("version", "p0", "stage_sha", "manifest_sha256", "main_sha", "evidence", "semantic_review"))
    require(type(data["version"]) is int and data["version"] == 1 and data["main_sha"] == current
            and data["semantic_review"] is True, "capitalization review missing or stale")
    parent = number(data["p0"])
    require(parent not in (8195, control), "invalid capitalization parent")
    require(event_issue["title"] == PURGE_TITLE + str(parent), "capitalization control title mismatch")
    commit = checked_sha(data["stage_sha"])
    raw = stage.read_remote(parent, commit)
    digest = sha256(raw)
    require(digest == data["manifest_sha256"], "staging digest mismatch")
    manifest = strict_json(raw)
    require(manifest["version"] == 1 and manifest["repository"] == REPO and manifest["p0"] == parent,
            "invalid staged manifest")
    evidence = data["evidence"]
    require(type(evidence) is list and 1 <= len(evidence) <= 30, "missing canonical evidence")
    paths = set()
    for entry in evidence:
        exact_keys(entry, ("path", "sha256"))
        path = entry["path"]
        require(path not in paths and sha256(evidence_at(current, path)) == entry["sha256"],
                "canonical evidence changed")
        paths.add(path)
    snap = snapshot(api, parent)
    require(any(c["body"] == DONE + digest + " " + commit for c in snap["comments"]),
            "consolidation not complete")
    require(not any(c["body"].startswith(PURGED) for c in snap["comments"]), "purge already consumed")
    main_guard(api, current)
    require(snapshot(api, control) == control_before and snapshot(api, parent) == snap,
            "capitalization corpus drift")
    require(stage.read_remote(parent, commit) == raw, "staging changed")
    marker = PURGED + digest + " " + commit + " review #" + str(control)
    response = api.mark(parent, marker)
    require(response.get("body") == marker, "purge journal mismatch")
    expected_change(snap, snapshot(api, parent), marker=response)
    main_guard(api, current)
    require(snapshot(api, control) == control_before, "capitalization control changed")
    stage.purge(parent, commit)
    return {"status": "STAGING_REFERENCE_PURGED", "p0": parent, "stage_sha": commit,
            "physical_erasure_proven": False, "semantic_equivalence_proven_by_cli": False}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("audit", "stage-preview", "closure"))
    parser.add_argument("--issues", default="")
    args = parser.parse_args(argv)
    try:
        event_issue = closure_event(os.environ) if args.mode != "audit" else None
        current = head()
        token = os.environ.get("GITHUB_TOKEN", "")
        api = (WriteAPI if args.mode == "closure" else ReadOnlyAPI)(token)
        if args.mode != "audit":
            require(current == os.environ.get("GITHUB_SHA"), "checkout mismatch")
            if args.mode == "stage-preview":
                result = local_stage_roundtrip(collect_closure(api, event_issue, current))
            else:
                with tempfile.TemporaryDirectory(prefix="backlog-stage-") as directory:
                    stage = GitStage(directory, token)
                    result = (purge_capitalized if event_issue["title"].startswith(PURGE_TITLE)
                              else consolidate)(api, stage, event_issue, current)
        else:
            require(bool(re.fullmatch(r"[1-9]\d*(?:,[1-9]\d*){0,2}", args.issues)), "invalid audit list")
            numbers = issue_numbers([int(n) for n in args.issues.split(",")])
            result = {"status": "READ_ONLY_AUDIT", "main_sha": current,
                      "issues": [audit_issue(snapshot(api, n)) for n in numbers],
                      "documents": doc_report(doc_texts(current)), "destruction_enabled": False}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Refusal as exc:
        print("REFUSED: " + str(exc), file=sys.stderr)
        return 2
    except (KeyError, TypeError, ValueError, UnicodeError, OverflowError, RecursionError, OSError, StopIteration):
        print("REFUSED: malformed input or response", file=sys.stderr)
        return 2

if __name__ == "__main__":
    sys.exit(main())
