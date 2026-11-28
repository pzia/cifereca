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
STANDARD_CORPUS_LIMIT = 20
LOT_BATCH = 10
XL_GROUP_SIZES = (5, 10)
XL_CORPUS_LIMIT = 31  # anchor + ten original controls + twenty members
LOT_STAGE_LIMIT = 8 * 1024 * 1024

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

def issue_numbers(values, limit=3):
    require(type(values) is list and 1 <= len(values) <= limit,
            "corpus issue limit exceeded")
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
        require(marker.startswith(("BACKLOG-STAGING-START v1 ", "BACKLOG-STAGING-DONE v1 ", "BACKLOG-STAGING-PURGE-START v1 ", "BACKLOG-STAGING-PURGED v1 ", "BACKLOG-STAGING-PROGRESS v1 ")), "invalid staging marker")
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

def scan_snapshot(api, n, closed=True):
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
    require(len(comments) == first.get("comments"), "issue changed during scan")
    return first, last, sorted(comments, key=lambda c: c["id"])

def issue_timestamp_only(before, after):
    left, right = dict(before), dict(after)
    old, new = left.pop("updated_at", None), right.pop("updated_at", None)
    return (left == right and old is not None and new is not None
            and timestamp(old) <= timestamp(new))

def snapshot(api, n, closed=True):
    # A bounded full reread handles delayed issue updated_at after comment writes.
    # Never normalize the returned snapshot or retry substantive drift.
    prior_issue = prior_comments = None
    for _ in range(3):
        first, last, comments = scan_snapshot(api, n, closed)
        if prior_issue is not None:
            require((first == prior_issue or issue_timestamp_only(prior_issue, first))
                    and comments == prior_comments, "issue changed during scan")
        if first == last:
            return {"issue": last, "comments": comments}
        require(issue_timestamp_only(first, last), "issue changed during scan")
        prior_issue, prior_comments = last, comments
    raise Refusal("unstable issue timestamp during scan")

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

LOT_TITLE = "Lot consolidation #8195 — "
LOT_SCOPE = re.compile(r"(?m)^<!-- backlog-lot-v1: ([1-9]\d*) ; ([1-9]\d*(?:,[1-9]\d*)*) ; public -->$")
MASTER_SCOPE = re.compile(r"(?m)^<!-- backlog-master-lot-v1: ([1-9]\d*) ; ([1-9]\d*(?:,[1-9]\d*)*) ; public -->$")

XL_SCOPE = re.compile(r"(?m)^<!-- backlog-xl-v1: ([1-9]\d*) ; ([1-9]\d*(?:,[1-9]\d*)*) ; public -->$")
XL_CLAIM = "BACKLOG-XL-CLAIM v1 #"

def xl_controls(issue):
    matches = XL_SCOPE.findall(issue["body"] or "")
    if not matches:
        return []
    require(len(matches) == 1 and not LOT_SCOPE.findall(issue["body"] or ""),
            "ambiguous XL attachment")
    master, controls = matches[0]
    require(issue["body"] == f"<!-- backlog-xl-v1: {master} ; {controls} ; public -->",
            "invalid XL control body")
    controls = issue_numbers([int(n) for n in controls.split(",")], 10)
    require(len(controls) in XL_GROUP_SIZES, "XL group must contain five or ten lots")
    return controls

def validate_xl_claim(snap, master, group, declaration):
    issue = snap["issue"]
    n, members = declaration
    require(issue["number"] == int(n) and issue["title"] == LOT_TITLE + str(master)
            and issue["body"] == f"<!-- backlog-lot-v1: {master} ; {members} ; public -->"
            and issue["state"] == "closed" and issue["state_reason"] == "not_planned"
            and not issue["labels"] and not issue["assignees"]
            and issue["user"]["id"] == OWNER_ID, "invalid XL source control")
    comments = snap["comments"]
    require(len(comments) == 1 and comments[0]["body"] == XL_CLAIM + str(group)
            and comments[0]["user"]["id"] == OWNER_ID
            and comments[0]["user"]["login"] == "pzia", "missing or conflicting XL claim")

def lot_attachment(api, issue):
    selected = xl_controls(issue)
    matches = (XL_SCOPE if selected else LOT_SCOPE).findall(issue["body"] or "")
    require(len(matches) == 1, "invalid lot attachment")
    master, members = matches[0]
    master = int(master)
    members = selected or issue_numbers([int(n) for n in members.split(",")])
    require((selected or len(members) <= 2) and master not in members and master != issue["number"]
            and issue["number"] not in members and 8195 not in [master, *members],
            "invalid lot members")
    require(issue["title"] == LOT_TITLE + str(master), "lot master title mismatch")
    snap = snapshot(api, master)
    closure_identity(snap["issue"])
    require(not snap["issue"]["title"].startswith(LOT_TITLE), "nested lot master")
    require("PU" not in {l["name"] for l in snap["issue"]["labels"]}, "suspended master")
    require(not SCOPE.findall(snap["issue"]["body"] or ""), "mixed master modes")
    declarations = MASTER_SCOPE.findall(snap["issue"]["body"] or "")
    require(1 <= len(declarations) <= 100, "invalid master inventory")
    controls = [int(n) for n, _ in declarations]
    all_members = [int(n) for _, group in declarations for n in group.split(",")]
    require(len(set(controls)) == len(controls) and len(set(all_members)) == len(all_members)
            and not set(controls) & set(all_members)
            and master not in controls + all_members and 8195 not in controls + all_members,
            "overlapping master lots")
    require(all(1 <= len(group.split(",")) <= 2 for _, group in declarations),
            "invalid master lot size")
    if selected:
        require(issue["number"] not in controls + all_members, "XL anchor overlaps master")
        require(all(n in controls for n in selected), "XL source not bound to master")
        first = controls.index(selected[0])
        require(controls[first:first + len(selected)] == selected, "XL sources must be consecutive")
        chosen = declarations[first:first + len(selected)]
        require(all(len(group.split(",")) == 2 for _, group in chosen), "XL source must have two members")
        for declaration in chosen:
            validate_xl_claim(snapshot(api, int(declaration[0])), master, issue["number"], declaration)
        members = selected + [int(n) for _, group in chosen for n in group.split(",")]
    else:
        require((str(issue["number"]), ",".join(map(str, members))) in declarations,
                "lot not bound to master")
    return master, members, snap

def closure_identity(issue):
    if issue["title"].startswith(LOT_TITLE):
        require("pull_request" not in issue and issue["state"] == "closed"
                and issue["state_reason"] == "completed" and not issue["labels"]
                and not issue["assignees"] and issue["user"]["id"] == OWNER_ID,
                "invalid lot control")
        timestamp(issue["closed_at"])
        return {k: issue[k] for k in ("id", "number", "title", "body", "state",
                                      "state_reason", "closed_at", "labels")}
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
    lot = lot_attachment(api, event_issue) if identity["title"].startswith(LOT_TITLE) else None
    if lot:
        children = lot[1]
        require(not SCOPE.findall(identity["body"] or ""), "mixed lot modes")
    else:
        matches = SCOPE.findall(identity["body"] or "")
        require(len(matches) == 1, "missing or ambiguous public closure scope")
        children = [] if matches[0] == "none" else [int(n) for n in matches[0].split(",")]
    selected = xl_controls(event_issue) if lot else []
    numbers = issue_numbers([root, *children], XL_CORPUS_LIMIT if selected else (3 if lot else STANDARD_CORPUS_LIMIT))
    require(8195 not in numbers, "infrastructure issue excluded")
    snapshots = {n: snapshot(api, n) for n in numbers}
    require(closure_identity(snapshots[root]["issue"]) == identity, "closure event drift")
    edges = {}
    for n, snap in snapshots.items():
        require(n in selected or not any(c["body"].startswith(XL_CLAIM) for c in snap["comments"]),
                "corpus claimed by XL group")
        # snapshot() already requires closed state, independently of priority/reason.
        require(not any((text or "").startswith("Corpus transféré — P0 #")
                        for text in issue_texts(snap)), "corpus already transferred")
        require(not any(c["body"].startswith("BACKLOG-STAGING-") for c in snap["comments"]), "corpus already consumed")
        subs = api.get(f"/issues/{n}/sub_issues?per_page=100&page=1")
        require(type(subs) is list and len(subs) < 100, "ambiguous native sub-issues")
        ids = sorted(number(child["number"]) for child in subs)
        summary = snap["issue"].get("sub_issues_summary")
        require(type(summary) is dict and summary.get("total") == len(ids)
                and set(ids) <= set(children) - {n}, "unresolved native sub-issue")
        edges[n] = ids
    # The exact owner-attested master inventory is the explicit attachment.
    # Native edges, if present, must still be fully covered; no reverse mention required.
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
    if not lot:
        manifest["standard_batches"] = True
    if lot:
        require(snapshot(api, lot[0]) == lot[2], "master inventory drift")
        manifest["master"] = lot[0]
        manifest["master_sha256"] = fingerprint(lot[2])
        if selected:
            declarations = dict(MASTER_SCOPE.findall(lot[2]["issue"]["body"] or ""))
            for n in selected:
                validate_xl_claim(snapshots[n], lot[0], root, (str(n), declarations[str(n)]))
            manifest["xl_controls"] = selected
    require(len(encode(manifest)) <= (LOT_STAGE_LIMIT if lot else STAGE_LIMIT), "staging corpus too large")
    return manifest

STAGE_PREFIX = "refs/heads/staging/backlog/p0-"
START = "BACKLOG-STAGING-START v1 "
DONE = "BACKLOG-STAGING-DONE v1 "
PURGE_START = "BACKLOG-STAGING-PURGE-START v1 "
PURGED = "BACKLOG-STAGING-PURGED v1 "
PROGRESS = "BACKLOG-STAGING-PROGRESS v1 "
CONTINUE_TITLE = "Poursuite consolidation #8195 — "
PURGE_TITLE = "Capitalisation #8195 — "
RECEIVE_TITLE = "Réception consolidation #8195 — "
RECOVER_TITLE = "Nouvelle consolidation #8195 — "

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
        require(0 < len(raw) <= LOT_STAGE_LIMIT, "staging corpus too large")
        blob = self.run("hash-object", "-w", "--stdin", data=raw).strip()
        tree = self.run("mktree", data=b"100644 blob " + blob + b"\tmanifest.json\n").strip()
        return checked_sha(self.run("commit-tree", tree.decode("ascii"),
                           data=b"Temporary P0 staging\n").decode("ascii").strip())

    def read_commit(self, commit):
        checked_sha(commit)
        require(self.run("rev-list", "--count", commit).strip() == b"1", "staging has ancestry")
        tree = self.run("ls-tree", commit).strip()
        require(bool(re.fullmatch(rb"100644 blob [0-9a-f]{40}\tmanifest\.json", tree)), "unexpected staging tree")
        size = int(self.run("cat-file", "-s", commit + ":manifest.json").strip())
        require(0 < size <= LOT_STAGE_LIMIT, "invalid staged size")
        raw = self.run("show", commit + ":manifest.json")
        require(0 < len(raw) <= LOT_STAGE_LIMIT, "invalid staged size")
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
    if not text or len(text.encode("utf-8")) < 256:
        return text
    refs = sorted(set(REFERENCE.findall(text)))
    stub = "Corpus transféré — P0 #" + str(parent) + " (consolidation #8195)."
    if refs:
        stub += "\nRéférences : " + " ".join(refs)
    # Both metrics must decrease. Keep short comments and their unique links intact.
    return stub if len(stub) < len(text) and len(stub.encode()) < len(text.encode()) else text

def same_after_write(before, after):
    """Allow only monotonic issue timestamp drift after our verified mutation."""
    if before == after:
        return True
    return (issue_timestamp_only(before["issue"], after["issue"])
            and before["comments"] == after["comments"])

def lot_plan(manifest):
    """Stable write order based solely on the verified original manifest."""
    parent = manifest["p0"]
    mutations = []
    for snap in manifest["snapshots"]:
        n = snap["issue"]["number"]
        old = snap["issue"]["body"]
        new = old if n == parent and "master" in manifest else compact_text(old, parent)
        if old != new:
            mutations.append((n, None, new))
        for c in snap["comments"]:
            new = compact_text(c["body"], parent)
            if new != c["body"]:
                mutations.append((n, c["id"], new))
    return mutations

def progress_marker(digest, commit, count):
    return PROGRESS + digest + " " + commit + " " + str(count)

def expected_prefix(manifest, count):
    """Return original public projections with precisely the first count writes applied."""
    result = strict_json(encode(manifest["snapshots"]))
    by_number = {s["issue"]["number"]: s for s in result}
    mutations = lot_plan(manifest)
    require(0 <= count <= len(mutations), "invalid progress cursor")
    for n, cid, body in mutations[:count]:
        snap = by_number[n]
        snap["issue"].pop("updated_at", None)
        if cid is None:
            snap["issue"]["body"] = body
        else:
            c = next(c for c in snap["comments"] if c["id"] == cid)
            c["body"] = body
            c.pop("updated_at", None)
    return result

def verify_lot_prefix(api, manifest, digest, commit, count, journal_count=None):
    """Verify the entire real prefix separately from the durable journal cursor."""
    if journal_count is None:
        journal_count = count
    require(0 <= journal_count <= count <= journal_count + LOT_BATCH,
            "invalid partial prefix")
    parent = manifest["p0"]
    if "master" in manifest:
        require(fingerprint(snapshot(api, manifest["master"])) == manifest["master_sha256"],
                "master inventory drift")
    states = {}
    originals = {s["issue"]["number"]: s for s in manifest["snapshots"]}
    for expected in expected_prefix(manifest, count):
        n = expected["issue"]["number"]
        snap = snapshot(api, n)
        states[n] = snap
        observed = public_projection(snap)
        journals = [c for c in observed["comments"] if c["body"].startswith("BACKLOG-STAGING-")]
        if n == parent:
            markers = [START + digest + " " + commit] + [
                progress_marker(digest, commit, i) for i in range(LOT_BATCH, journal_count + 1, LOT_BATCH)]
            require([c["body"] for c in journals] == markers
                    and all(c["author"] == {"login": "github-actions[bot]", "id": 41898282}
                            for c in journals), "lot journal or cursor mismatch")
            if "master" in manifest:
                master, members, _ = lot_attachment(api, snap["issue"])
                require(master == manifest["master"] and set(members) == set(originals) - {parent}
                        and xl_controls(snap["issue"]) == manifest.get("xl_controls", []),
                        "lot attachment drift")
            observed["comments"] = [c for c in observed["comments"] if c not in journals]
            expected["issue"].pop("updated_at", None)
        else:
            require(not journals, "unexpected child journal")
        original = originals[n]
        if "updated_at" not in expected["issue"]:
            require(timestamp(original["issue"]["updated_at"]) <= timestamp(observed["issue"].pop("updated_at")),
                    "issue timestamp regressed")
        for old, want, seen in zip(original["comments"], expected["comments"], observed["comments"]):
            if "updated_at" not in want:
                require(timestamp(old["updated_at"]) <= timestamp(seen.pop("updated_at")),
                        "comment timestamp regressed")
        require(observed == expected, "incomplete or divergent lot prefix")
        subs = api.get(f"/issues/{n}/sub_issues?per_page=100&page=1")
        require(type(subs) is list and len(subs) < 100
                and snap["issue"].get("sub_issues_summary", {}).get("total") == len(subs)
                and sorted(number(child["number"]) for child in subs) == manifest["edges"][str(n)], "scope drift")
    return states

def consolidate(api, stage, event_issue, current, clock=None, recovery=None):
    clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
    manifest = collect_closure(api, event_issue, current)
    parent = manifest["p0"]
    if recovery is not None:
        control, control_before, expected_digest = recovery
        require("master" not in manifest and control not in [s["issue"]["number"] for s in manifest["snapshots"]]
                and sha256(encode(manifest)) == expected_digest, "recovery corpus mismatch")
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
        new = old if n == parent and "master" in manifest else compact_text(old, parent)
        if old != new:
            mutations.append((n, None, new))
        projected.append(new or "")
        for c in snap["comments"]:
            new = compact_text(c["body"], parent)
            if new != c["body"]:
                mutations.append((n, c["id"], new))
            projected.append(new)
    require(mutations == lot_plan(manifest), "unstable mutation plan")
    before = metrics([t for snap in states.values() for t in issue_texts(snap)])
    # Count both persistent journals, even before the commit SHA is known.
    progress = [progress_marker(digest, "0" * 40, i) for i in range(LOT_BATCH, len(mutations), LOT_BATCH)]
    estimate = metrics(projected + [START + digest + " " + "0" * 40, DONE + digest + " " + "0" * 40] + progress)
    require(all(before[k] > estimate[k] for k in ("characters", "bytes")), "no positive contextual gain")
    commit = None
    written = set()
    def guard():
        if "master" in manifest:
            require(fingerprint(snapshot(api, manifest["master"])) == manifest["master_sha256"],
                    "master inventory drift")
        authorization = event_issue if recovery is None else control_before["issue"]
        require(clock() < timestamp(authorization["closed_at"]) + dt.timedelta(hours=24), "closure expired")
        if recovery is not None:
            require(snapshot(api, control) == control_before, "recovery control changed")
        main_guard(api, current)
        for n, snap in states.items():
            observed = snapshot(api, n)
            require(observed == snap or (n in written and same_after_write(snap, observed)), "corpus drift")
            states[n] = observed
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
        written.add(parent)
    mark(START + digest + " " + commit)
    batch = mutations[:LOT_BATCH]
    for n, cid, body in batch:
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
        written.add(n)
    guard()
    if len(batch) < len(mutations):
        mark(progress_marker(digest, commit, len(batch)))
        guard()
        return {"status": "LOT_PENDING", "p0": parent, "stage_sha": commit,
                "manifest_sha256": digest, "cursor": len(batch), "total": len(mutations)}
    mark(DONE + digest + " " + commit)
    guard()
    after = metrics([t for snap in states.values() for t in issue_texts(snap)])
    return {"status": "STAGED_AND_COMPACTED", "p0": parent, "stage_sha": commit,
            "manifest_sha256": digest, "staging_bytes": len(raw), "before": before, "after": after,
            "context_gain": {k: before[k] - after[k] for k in before}, "capitalized": False,
            "global_gain_proven": False}

def continue_lot(api, stage, event_issue, current):
    require(event_issue["state"] == "closed" and event_issue["state_reason"] == "completed"
            and not event_issue["labels"] and not event_issue["assignees"]
            and event_issue["user"]["id"] == OWNER_ID, "invalid continuation control")
    control = number(event_issue["number"])
    control_before = snapshot(api, control)
    require(all(control_before["issue"].get(k) == event_issue.get(k) for k in
                ("id", "number", "body", "title", "state", "state_reason", "closed_at", "labels", "assignees")),
            "continuation control changed")
    data = strict_json(event_issue["body"].encode())
    keys = ("version", "p0", "stage_sha", "manifest_sha256", "main_sha", "cursor")
    require(type(data.get("version")) is int and data["version"] in (1, 2), "invalid continuation version")
    recovery = data["version"] == 2
    exact_keys(data, keys + ("applied",) if recovery else keys)
    require(data["main_sha"] == current, "stale continuation")
    parent = number(data["p0"])
    require(parent not in (8195, control) and event_issue["title"] == CONTINUE_TITLE + str(parent),
            "invalid continuation parent")
    commit = checked_sha(data["stage_sha"])
    raw = stage.read_remote(parent, commit)
    digest = sha256(raw)
    require(digest == data["manifest_sha256"], "staging digest mismatch")
    manifest = strict_json(raw)
    require(manifest["version"] == 1 and manifest["repository"] == REPO and manifest["p0"] == parent
            and ("master" in manifest or manifest.get("standard_batches") is True), "invalid lot manifest")
    numbers = issue_numbers([x["issue"]["number"] for x in manifest["snapshots"]],
                            XL_CORPUS_LIMIT if "xl_controls" in manifest else (3 if "master" in manifest else STANDARD_CORPUS_LIMIT))
    require(parent in numbers and control not in numbers and 8195 not in numbers, "invalid staged corpus")
    mutations = lot_plan(manifest)
    cursor = data["cursor"]
    require(type(cursor) is int and 0 <= cursor < len(mutations) and cursor % LOT_BATCH == 0
            and (recovery or cursor > 0), "invalid progress cursor")
    end = min(cursor + LOT_BATCH, len(mutations))
    applied = data["applied"] if recovery else cursor
    require(type(applied) is int and cursor <= applied <= end
            and (not recovery or cursor == 0 or applied > cursor), "invalid applied prefix")
    states = verify_lot_prefix(api, manifest, digest, commit, applied, journal_count=cursor)
    written = set()
    def guard():
        main_guard(api, current)
        require(snapshot(api, control) == control_before, "continuation control changed")
        if "master" in manifest:
            require(fingerprint(snapshot(api, manifest["master"])) == manifest["master_sha256"], "master inventory drift")
        for n, snap in states.items():
            observed = snapshot(api, n)
            require(observed == snap or (n in written and same_after_write(snap, observed)), "corpus drift")
            states[n] = observed
            subs = api.get(f"/issues/{n}/sub_issues?per_page=100&page=1")
            require(sorted(number(child["number"]) for child in subs) == manifest["edges"][str(n)], "scope drift")
        require(stage.remote_head(parent) == commit, "staging ref drift")
        main_guard(api, current)
    guard()
    for n, cid, body in mutations[applied:end]:
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
        written.add(n)
    guard()
    marker = DONE + digest + " " + commit if end == len(mutations) else progress_marker(digest, commit, end)
    response = api.mark(parent, marker)
    require(response.get("body") == marker, "continuation journal mismatch")
    after = snapshot(api, parent)
    expected_change(states[parent], after, marker=response)
    states[parent] = after
    written.add(parent)
    guard()
    return {"status": "STAGED_AND_COMPACTED" if end == len(mutations) else "LOT_PENDING",
            "p0": parent, "stage_sha": commit, "manifest_sha256": digest, "cursor": end, "total": len(mutations)}

def recover_unstarted(api, stage, event_issue, current, clock=None):
    """Fresh owner control, never a rerun; bind the reviewed untouched corpus."""
    require(event_issue["state"] == "closed" and event_issue["state_reason"] == "completed"
            and not event_issue["labels"] and not event_issue["assignees"]
            and event_issue["user"]["id"] == OWNER_ID, "invalid recovery control")
    control = number(event_issue["number"])
    before = snapshot(api, control)
    require(all(before["issue"].get(k) == event_issue.get(k) for k in
                ("id", "number", "body", "title", "state", "state_reason", "closed_at", "labels", "assignees")),
            "recovery control changed")
    data = strict_json(event_issue["body"].encode())
    exact_keys(data, ("version", "p0", "main_sha", "manifest_sha256", "unstarted_review"))
    parent = number(data["p0"])
    require(type(data["version"]) is int and data["version"] == 1
            and data["main_sha"] == current and data["unstarted_review"] is True
            and parent not in (8195, 8207, control)
            and event_issue["title"] == RECOVER_TITLE + str(parent), "invalid recovery authorization")
    target = snapshot(api, parent)["issue"]
    require(not target["title"].startswith(LOT_TITLE), "historical recovery excluded")
    require(stage.remote_head(parent) is None, "staging already exists")
    return consolidate(api, stage, target, current, clock=clock,
                       recovery=(control, before, data["manifest_sha256"]))


def canonical_evidence(ref, path):
    checked_sha(ref)
    require(type(path) is str and (path == "AGENTS.md" or
            bool(re.fullmatch(r"(?:docs|\.agents/roles|\.agents/skills)/[A-Za-z0-9_./-]+\.md", path)))
            and all(part not in ("", ".", "..") for part in path.split("/")), "invalid evidence path")
    return git("show", ref + ":" + path)

def verify_compacted_corpus(api, manifest, digest, commit, control, completed=False):
    """Verify the complete compacted projection and exact bot journals."""
    parent = manifest["p0"]
    originals = manifest["snapshots"]
    numbers = issue_numbers([x["issue"]["number"] for x in originals],
                            XL_CORPUS_LIMIT if "xl_controls" in manifest else (3 if "master" in manifest else STANDARD_CORPUS_LIMIT))
    if "master" in manifest:
        require(fingerprint(snapshot(api, manifest["master"])) == manifest["master_sha256"], "master inventory drift")
    require(parent in numbers and control not in numbers and 8195 not in numbers,
            "invalid staged corpus")
    states = {}
    start = START + digest + " " + commit
    for original in originals:
        n = original["issue"]["number"]
        snap = snapshot(api, n)
        states[n] = snap
        projected = public_projection(snap)
        journals = [c for c in projected["comments"] if c["body"].startswith("BACKLOG-STAGING-")]
        if n == parent:
            if "xl_controls" in manifest:
                master, members, _ = lot_attachment(api, snap["issue"])
                require(master == manifest["master"] and set(members) == set(numbers) - {parent}
                        and xl_controls(snap["issue"]) == manifest["xl_controls"], "XL attachment drift")
            expected_journals = [start]
            if "master" in manifest or manifest.get("standard_batches") is True:
                expected_journals += [progress_marker(digest, commit, i) for i in range(LOT_BATCH, len(lot_plan(manifest)), LOT_BATCH)]
            if completed:
                expected_journals.append(DONE + digest + " " + commit)
            require([c["body"] for c in journals] == expected_journals
                    and all(c["author"] == {"login": "github-actions[bot]", "id": 41898282}
                            for c in journals), "missing exact START or progress")
        else:
            require(not journals, "unexpected child journal")
        projected["comments"] = [c for c in projected["comments"] if c not in journals]
        expected = strict_json(encode(original))
        if n != parent or "master" not in manifest:
            expected["issue"]["body"] = compact_text(expected["issue"]["body"], parent)
        # Compare preserved public provenance; server timestamps are allowed to change
        # only for items which were compacted (parent also received the journal).
        if n == parent or expected["issue"]["body"] != original["issue"]["body"]:
            expected["issue"].pop("updated_at")
            projected["issue"].pop("updated_at")
        for old, want, seen in zip(original["comments"], expected["comments"], projected["comments"]):
            want["body"] = compact_text(old["body"], parent)
            if want["body"] != old["body"]:
                want.pop("updated_at")
                seen.pop("updated_at")
        require(expected == projected, "incomplete or divergent compacted corpus")
        subs = api.get(f"/issues/{n}/sub_issues?per_page=100&page=1")
        require(sorted(number(child["number"]) for child in subs) == manifest["edges"][str(n)], "scope drift")
    return states


def receive_completed(api, stage, event_issue, current):
    """Finalize only an already completed deterministic transfer; never edit targets."""
    require(event_issue["state"] == "closed" and event_issue["state_reason"] == "completed"
            and not event_issue["labels"] and not event_issue["assignees"]
            and event_issue["user"]["id"] == OWNER_ID, "invalid reception control")
    control = number(event_issue["number"])
    control_before = snapshot(api, control)
    require(all(control_before["issue"].get(k) == event_issue.get(k) for k in
                ("id", "number", "body", "title", "state", "state_reason", "closed_at", "labels", "assignees")),
            "reception control changed")
    data = strict_json(event_issue["body"].encode())
    exact_keys(data, ("version", "p0", "stage_sha", "manifest_sha256", "main_sha", "reviewed"))
    require(type(data["version"]) is int and data["version"] == 1 and data["main_sha"] == current
            and data["reviewed"] is True, "reception review missing or stale")
    parent = number(data["p0"])
    require(parent not in (8195, control) and event_issue["title"] == RECEIVE_TITLE + str(parent),
            "invalid reception parent")
    commit = checked_sha(data["stage_sha"])
    raw = stage.read_remote(parent, commit)
    digest = sha256(raw)
    require(digest == data["manifest_sha256"], "staging digest mismatch")
    manifest = strict_json(raw)
    require(manifest["version"] == 1 and manifest["repository"] == REPO and manifest["p0"] == parent,
            "invalid staged manifest")
    states = verify_compacted_corpus(api, manifest, digest, commit, control)
    main_guard(api, current)
    require(snapshot(api, control) == control_before and all(snapshot(api, n) == snap for n, snap in states.items()),
            "reception corpus drift")
    require(stage.read_remote(parent, commit) == raw, "staging changed")
    main_guard(api, current)
    marker = DONE + digest + " " + commit
    response = api.mark(parent, marker)
    require(response.get("body") == marker, "reception journal mismatch")
    expected_change(states[parent], snapshot(api, parent), marker=response)
    return {"status": "RECEIVED_ALREADY_COMPACTED", "p0": parent, "stage_sha": commit,
            "manifest_sha256": digest, "destructive_mutations": 0, "capitalized": False}

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
    if "master" in manifest:
        require(fingerprint(snapshot(api, manifest["master"])) == manifest["master_sha256"], "master inventory drift")
    evidence = data["evidence"]
    require(type(evidence) is list and 1 <= len(evidence) <= 30, "missing canonical evidence")
    paths = set()
    for entry in evidence:
        exact_keys(entry, ("path", "sha256"))
        path = entry["path"]
        require(path not in paths and sha256(evidence_at(current, path)) == entry["sha256"],
                "canonical evidence changed")
        paths.add(path)
    states = verify_compacted_corpus(api, manifest, digest, commit, control, completed=True)
    snap = states[parent]
    main_guard(api, current)
    require(snapshot(api, control) == control_before and all(snapshot(api, n) == original for n, original in states.items()),
            "capitalization corpus drift")
    require(stage.read_remote(parent, commit) == raw, "staging changed")
    marker = PURGE_START + digest + " " + commit + " review #" + str(control)
    response = api.mark(parent, marker)
    require(response.get("body") == marker, "purge journal mismatch")
    after_start = snapshot(api, parent)
    expected_change(snap, after_start, marker=response)
    main_guard(api, current)
    require(snapshot(api, control) == control_before, "capitalization control changed")
    require(snapshot(api, parent) == after_start and all(snapshot(api, n) == original for n, original in states.items() if n != parent), "capitalization corpus drift")
    if "master" in manifest:
        require(fingerprint(snapshot(api, manifest["master"])) == manifest["master_sha256"], "master inventory drift")
    stage.purge(parent, commit)
    marker = PURGED + digest + " " + commit + " review #" + str(control)
    response = api.mark(parent, marker)
    require(response.get("body") == marker, "purge completion journal mismatch")
    expected_change(after_start, snapshot(api, parent), marker=response)
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
                    operation = (purge_capitalized if event_issue["title"].startswith(PURGE_TITLE)
                                 else receive_completed if event_issue["title"].startswith(RECEIVE_TITLE)
                                 else recover_unstarted if event_issue["title"].startswith(RECOVER_TITLE)
                                 else continue_lot if event_issue["title"].startswith(CONTINUE_TITLE)
                                 else consolidate)
                    result = operation(api, stage, event_issue, current)
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
