#!/usr/bin/env python3
"""Refuse retargeted tags and changes to published Core releases (#2095).

Read-only preflight. Runs before building and again before touching any GitHub
Release draft. Release lookup errors other than HTTP 404 fail closed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class ReleaseRefused(ValueError):
    pass


def validate(tag: str, version: str, commit: str, tag_commit: str | None,
             release: dict | None) -> str:
    if (type(version) is not str
            or re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}[0-9A-Za-z.+_-]*", version) is None
            or tag != "v" + version
            or re.fullmatch(r"[0-9a-f]{40}", commit) is None):
        raise ReleaseRefused("Invalid Core release version, tag or commit")
    if tag_commit is not None and tag_commit != commit:
        raise ReleaseRefused("Core release tag belongs to a different commit")
    if release is None:
        return "new"
    if (type(release) is not dict or release.get("tag_name") != tag
            or release.get("draft") is not True):
        raise ReleaseRefused("Published or invalid Core release is immutable")
    if release.get("target_commitish") != commit:
        raise ReleaseRefused("Existing Core draft targets a different commit")
    return "same-commit-draft"


def resolve_tag_commit(tag: str) -> str | None:
    exists = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", "refs/tags/" + tag],
        capture_output=True, check=False,
    )
    if exists.returncode == 1:
        return None
    if exists.returncode != 0:
        raise ReleaseRefused("Cannot verify the existing release tag")
    target = subprocess.run(
        ["git", "rev-parse", "--verify", "refs/tags/" + tag + "^{commit}"],
        capture_output=True, text=True, check=False,
    )
    if target.returncode or re.fullmatch(r"[0-9a-f]{40}", target.stdout.strip()) is None:
        raise ReleaseRefused("Invalid existing Core release tag")
    return target.stdout.strip()


def read_release(repository: str, tag: str, token: str) -> dict | None:
    if (re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None
            or not token):
        raise ReleaseRefused("GitHub release lookup unavailable")
    path = "/repos/" + repository + "/releases/tags/" + quote(tag, safe="")
    request = Request(
        "https://api.github.com" + path,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "cifereca-core-release-preflight",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            data = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise ReleaseRefused("GitHub release lookup unavailable") from None
    except (OSError, URLError, ValueError, UnicodeError):
        raise ReleaseRefused("GitHub release lookup unavailable") from None
    if type(data) is not dict:
        raise ReleaseRefused("GitHub release lookup returned invalid metadata")
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()
    try:
        # Validate untrusted CLI inputs before interpolating tag into git.
        validate(args.tag, args.version, args.commit, None, None)
        release = read_release(
            args.repository, args.tag, os.environ.get("GH_TOKEN", ""),
        )
        result = validate(
            args.tag, args.version, args.commit,
            resolve_tag_commit(args.tag), release,
        )
    except ReleaseRefused as exc:
        print("Core release refused: " + str(exc))
        return 2
    print("Core release target verified: " + result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
