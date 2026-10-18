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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    try:
        validate(json.load(sys.stdin), args.tag, args.commit)
    except (ValueError, TypeError) as exc:
        print("Plugins-only release refused: " + str(exc), file=sys.stderr)
        return 2
    print("Plugins-only draft source verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
