"""Manually capture publisher HTML for offline DOM qualification (#952 / #950).

Only explicit, reviewed public URLs are selectable. Output stays outside the
repository and must be manually scrubbed before inclusion as a test fixture.
Importing this module and running tests NEVER makes a network request.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_BYTES = 1_500_000
URLS = {
    "rageot-catalog": "https://www.rageot.fr/livres-rageot/",
    "rageot-page-2": "https://www.rageot.fr/livres-rageot/2/",
    "rageot-age-8": "https://www.rageot.fr/livres-8-ans-et-plus/",
    "rageot-book": "https://www.rageot.fr/livre/quatre-soeurs-et-la-magie-de-noel-9782700288506/",
    "bayard-book": "https://www.bayard-editions.com/jeunesse/petite-enfance/lecture-avec-les-petits/les-larmes/",
}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _open(req):
    # Explicitly refuse redirects, even to a second URL on the same domain.
    return build_opener(_NoRedirect()).open(req, timeout=12)


def validate_response(body, *, status, final_url, expected_url, content_type):
    """Reject non-HTML, errors, challenge pages, oversized and redirected content."""
    if status != 200:
        raise ValueError("HTTP response is not 200")
    if final_url != expected_url:
        raise ValueError("Unexpected final URL")
    if not str(content_type).lower().split(";", 1)[0].strip() == "text/html":
        raise ValueError("Response is not HTML")
    if not isinstance(body, bytes) or not body or len(body) > MAX_BYTES:
        raise ValueError("Missing or oversized HTML")
    sample = body[:120_000].lower()
    if b"<html" not in sample and b"<!doctype html" not in sample:
        raise ValueError("No HTML document boundary")
    if any(marker in sample for marker in (
        b"cf-chl-", b"cloudflare challenge", b"checking your browser",
        b"just a moment...</title>", b"captcha challenge",
    )):
        raise ValueError("Publisher returned a challenge, not a catalogue")


def capture(label, output_dir, *, opener=None):
    """Write raw response only to a private directory OUTSIDE the repository."""
    if label not in URLS:
        raise ValueError("Unknown fixture label")
    directory = Path(output_dir).expanduser().resolve()
    repository = Path(__file__).resolve().parents[4]
    if directory == repository or repository in directory.parents:
        raise ValueError("Raw unredacted HTML must not be placed in this repository")
    url = URLS[label]
    req = Request(url, headers={
        "User-Agent": "Cifereca-Youth-Source-Qualification/1.0",
        "Accept": "text/html",
    }, method="GET")
    with (opener or _open)(req) as response:
        data = response.read(MAX_BYTES + 1)
        validate_response(
            data, status=response.status,
            final_url=response.geturl(), expected_url=url,
            content_type=response.headers.get("Content-Type", ""),
        )
    directory.mkdir(parents=True, exist_ok=True)
    raw_path = directory / (label + ".raw.html")
    metadata_path = directory / (label + ".capture.json")
    # Exclusive creation prevents accidental replacement of prior evidence.
    fd = os.open(raw_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(data)
    meta = {
        "source_url": url,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "fixture_label": label,
        "review_required": True,
        "warning": "RAW HTML. Remove scripts, trackers, dynamic IDs and private data before committing.",
    }
    metadata_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return raw_path, metadata_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", choices=tuple(URLS))
    parser.add_argument("--output-dir", required=True,
                        help="Private path outside git checkout, e.g. /tmp/cifereca-publisher-capture")
    args = parser.parse_args()
    raw, metadata = capture(args.fixture, args.output_dir)
    print("Captured:", raw)
    print("Provenance:", metadata)
    print("This is unreviewed raw HTML, NOT a safe fixture: scrub and verify manually.")


if __name__ == "__main__":
    main()
