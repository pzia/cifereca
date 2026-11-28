"""Offline comparison of two exported, same-account BnF loan observations.

Run this trusted installed module explicitly against a local private health
bundle. No session, transport, credentials, subprocess or provider calls.
The export's scope claim is not a new authentication attestation; a copied
or modified ZIP is not a trusted Core vault object. Output contains counts
only. A difference between observations is not proof of parser regression.
"""
from io import BytesIO
from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZipFile
from cifereca_bnf_pret_web import ProfileScope, parse_loans, MAX_HTML_BYTES

_MAX_BUNDLE = 63 * 1024 * 1024
_ACTIVITY = "https://pret.bnf.fr/my_profile/activity"


def _read(archive, name, limit):
    matches = [info for info in archive.infolist() if info.filename == name]
    if len(matches) != 1 or not 0 <= matches[0].file_size <= limit:
        raise ValueError("archive_member")
    with archive.open(matches[0]) as stream:
        data = stream.read(limit + 1)
    if len(data) != matches[0].file_size:
        raise ValueError("archive_member")
    return data


def _projection(payload, scope, expected_version):
    with ZipFile(BytesIO(payload)) as archive:
        index = json.loads(_read(archive, "index.json", 8 * 1024 * 1024))
        ref = index["session_ref"]
        meta = index["health_observation"]
        if (index.get("schema") != "cifereca.http-trace/1"
                or type(ref) is not str or not ref or len(ref) > 256
                or index.get("omitted_exchanges") != 0
                or meta.get("schema") != "cifereca.health-observation/1"
                or meta.get("capability") != "loans" or meta.get("version") != expected_version
                or type(index["exchanges"]) is not list or len(index["exchanges"]) != 1):
            raise ValueError("capture_scope")
        entry = index["exchanges"][0]
        request, response, body = entry["request"], entry["response"], entry["response_body"]
        if (entry.get("task_ref") != ref or entry.get("outcome") != "complete"
                or request.get("producer") != "provider.bnf-pret"
                or request.get("method") != "GET" or request.get("url") != _ACTIVITY
                or entry["request_body"]["observed_bytes"] != 0
                or response.get("status_code") != 200
                or body.get("state") != "complete"
                or body.get("observed_bytes") != body.get("stored_bytes")):
            raise ValueError("capture_incomplete")
        headers = {}
        for key, value in response["headers"]:
            key = key.lower()
            if key in ("content-type", "content-encoding"):
                if key in headers:
                    raise ValueError("capture_headers")
                headers[key] = value
        if headers.get("content-encoding", "identity").lower().strip() != "identity":
            raise ValueError("capture_encoding")
        content_type = headers.get("content-type", "").lower()
        media, _, params = content_type.partition(";")
        if media.strip() not in ("text/html", "application/xhtml+xml"):
            raise ValueError("capture_content")
        charset = params.strip().removeprefix("charset=").strip('"') if params else "utf-8"
        if charset not in ("utf-8", "us-ascii", "iso-8859-1", "windows-1252"):
            raise ValueError("capture_encoding")
        data = _read(archive, body["blob"], MAX_HTML_BYTES)
        if len(data) != body["stored_bytes"] or sha256(data).hexdigest() != body["sha256"]:
            raise ValueError("capture_digest")
        # This scope only binds the parser's two inputs to the same exported
        # account. It is not a live session or proof of a new authentication.
        parsed = parse_loans(data.decode(charset, errors="strict"), page_url=_ACTIVITY,
                             expected=scope, observed=scope)
        if not parsed.exhaustive or parsed.status != "observed":
            raise ValueError("parser_partial")
        return {row.loan_id: (row.resource_id, row.title, row.authors, row.due_at)
                for row in parsed.loans}


def compare_bundle(path, first=1, second=2):
    """Return closed counts; unsupported or unverifiable data remains unknown."""
    try:
        if type(first) is not int or type(second) is not int or first == second:
            raise ValueError("selection")
        with Path(path).open("rb") as stream:
            raw = stream.read(_MAX_BUNDLE + 1)
        if len(raw) > _MAX_BUNDLE:
            raise ValueError("bundle_limit")
        with ZipFile(BytesIO(raw)) as archive:
            manifest = json.loads(_read(archive, "manifest.json", 256000))
            if (manifest.get("schema") != "cifereca.provider-health-bundle/1"
                    or manifest.get("scope") != {"provider_id": "provider.bnf-pret", "single_private_account": True}):
                raise ValueError("bundle_scope")
            entries = manifest["captures"]
            if not 1 <= first <= len(entries) <= 25 or not 1 <= second <= len(entries):
                raise ValueError("selection")
            scope = ProfileScope("exported-account", "exported-account", "offline")
            projections = []
            versions = []
            for number in (first, second):
                row = entries[number - 1]
                if row["capability"] != "loans" or row["member"] != f"captures/{number:02d}.zip":
                    raise ValueError("selection")
                version = row["observed_provider_version"]
                if type(version) is not str or not version or version == "unknown":
                    raise ValueError("capture_version")
                capture = _read(archive, row["member"], _MAX_BUNDLE)
                if sha256(capture).hexdigest() != row["sha256"]:
                    raise ValueError("capture_digest")
                projections.append(_projection(capture, scope, version))
                versions.append(version)
        before, after = projections
        return {"outcome": "equal" if before == after else "different",
                "added": len(after.keys() - before.keys()),
                "removed": len(before.keys() - after.keys()),
                "changed": sum(before[key] != after[key] for key in before.keys() & after.keys()),
                "observed_version_changed": versions[0] != versions[1],
                "authentication": "historical_export_claim",
                "parser_regression": "not_attested"}
    except Exception:
        return {"outcome": "unknown"}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle")
    parser.add_argument("--first", type=int, default=1)
    parser.add_argument("--second", type=int, default=2)
    args = parser.parse_args()
    result = compare_bundle(args.bundle, args.first, args.second)
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(2 if result["outcome"] == "unknown" else 0)
