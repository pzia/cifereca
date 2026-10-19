#!/usr/bin/env python3
"""Fail closed when an existing plugins-only release draft points elsewhere.

The requested release tag alone is not a source identity: a previously created
draft can target another commit even while the workflow runs on main.
"""
from __future__ import annotations

import argparse
import json
import re
import sys


class ReleaseTargetRefused(ValueError):
    """Release metadata does not match the current authorized source."""


def validate(release: object, tag: str, commit: str) -> None:
    if (not isinstance(tag, str)
            or re.fullmatch(r"plugins-v[0-9A-Za-z.+_-]+", tag) is None
            or not isinstance(commit, str)
            or re.fullmatch(r"[0-9a-f]{40}", commit) is None):
        raise ReleaseTargetRefused("Invalid plugins-only release coordinates")
    if (type(release) is not dict
            or release.get("tag_name") != tag
            or release.get("draft") is not True
            or release.get("target_commitish") != commit):
        raise ReleaseTargetRefused(
            "Release is published or draft tag/source commit differs; refusing mutation"
        )


def lookup_draft(pages: object, tag: str) -> tuple[int, str, int] | None:
    """Resolve a draft by exact tag from paginated REST collections.

    GitHub's /releases/tags/<tag> endpoint is unreliable for untagged drafts.
    All pages are authenticated and the result is reduced to three validated
    scalars: release ID, source SHA and number of uploaded assets.
    """
    if type(tag) is not str or re.fullmatch(r"plugins-v[0-9A-Za-z.+_-]+", tag) is None:
        raise ReleaseTargetRefused("Invalid plugins-only release tag")
    if type(pages) is not list:
        raise ReleaseTargetRefused("Invalid GitHub releases collection")
    matches: list[dict] = []
    for page in pages:
        if type(page) is not list:
            raise ReleaseTargetRefused("Invalid GitHub releases page")
        for entry in page:
            if type(entry) is not dict:
                raise ReleaseTargetRefused("Invalid GitHub release entry")
            if entry.get("tag_name") == tag:
                matches.append(entry)
                if len(matches) > 1:
                    raise ReleaseTargetRefused("Ambiguous duplicate release drafts")
    if not matches:
        return None
    release = matches[0]
    release_id = release.get("id")
    source = release.get("target_commitish")
    assets = release.get("assets")
    if (release.get("draft") is not True
            or type(release_id) is not int or release_id <= 0
            or type(source) is not str
            or re.fullmatch(r"[0-9a-f]{40}", source) is None
            or type(assets) is not list
            or any(type(asset) is not dict for asset in assets)):
        raise ReleaseTargetRefused("Untrusted draft release metadata")
    return release_id, source, len(assets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--lookup-draft", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        if args.lookup_draft:
            if re.fullmatch(r"[0-9a-f]{40}", args.commit) is None:
                raise ReleaseTargetRefused("Invalid plugins-only source SHA")
            found = lookup_draft(payload, args.tag)
            if found is None:
                print("none")
            else:
                print(f"{found[0]} {found[1]} {found[2]}")
            return 0
        validate(payload, args.tag, args.commit)
    except (ValueError, TypeError) as exc:
        print("Plugins-only release refused: " + str(exc), file=sys.stderr)
        return 2
    print("Plugins-only draft source verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
