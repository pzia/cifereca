"""#3252: anonymous, offline summary of Kobo final-delivery rejections.

Use: python -m scripts.kobo_final_verdict_summary PRIVATE-JOURNAL.zip
The ZIP is not copied, uploaded, extracted or modified. Only closed counters
are printed; never print raw events, item IDs, credentials or exception text.
An occurrence count is NOT a count of distinct books or independent syncs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re

from cifereca.journal_zip_contract import JournalZipError, read_raw_journal_zip


_PREFIX = "TRACE canonical delivery final rejected "
_FIELD = {
    "inspection_valid": frozenset(("True", "False")),
    "inspection_format": frozenset(("epub", "pdf", "None")),
    "inspection_protected": frozenset(("True", "False", "None")),
    "catalog_valid": frozenset(("True", "False")),
}
_ENCRYPTION_PROFILES = frozenset((
    "adobe_adept_marker", "aes_algorithm",
    "font_obfuscation_algorithms_only", "aes_marker_without_algorithm",
    "generic_xmlenc_marker", "other_encryption_marker",
    "encryption_manifest_missing", "encryption_manifest_malformed",
    "encryption_manifest_oversized", "encryption_manifest_unreadable",
))
_ENCRYPTION_PATTERN = re.compile(r"(?:^| )encryption_profile=([^ ]+)(?: |$)")
_RUNTIME_READER = "downloadable_selected_delivery_candidate_runtime_guarded"
_DIRECT_READER = "downloadable_selected_delivery_candidate_guarded"
_READER_PATTERN = re.compile(r"(?:^| )reader=([^ ]+)(?: |$)")
_RUNTIME_PATTERN = re.compile(r"(?:^| )runtime_guarded=(True|False)(?: |$)")



_PATTERN = {
    key: re.compile(r"(?:^| )" + re.escape(key) + r"=([^ ]+)(?: |$)")
    for key in _FIELD
}


def classify(events: object) -> dict[str, object]:
    """Return only closed diagnostic counts; never propagate private fields."""
    counts: Counter[str] = Counter()
    profiles: Counter[str] = Counter()
    route_profiles: Counter[str] = Counter()
    route_unknown = 0
    matched = 0
    complete = 0
    if type(events) is not list:
        return {"status": "invalid_events", "occurrences": 0,
                "instrumented": 0, "reasons": {}}
    for event in events:
        if type(event) is not dict:
            continue
        message = event.get("message")
        if (type(message) is not str or len(message) > 16384
                or not message.startswith(_PREFIX)
                or "code=canonical_delivery_final_invalid " not in message
                or "provider_id=kobo " not in message):
            continue
        matched += 1
        profile_match = _ENCRYPTION_PATTERN.search(message)
        profile = (
            profile_match.group(1)
            if profile_match and profile_match.group(1) in _ENCRYPTION_PROFILES
            else None
        )
        if profile is not None:
            profiles[profile] += 1
            reader_match = _READER_PATTERN.search(message)
            runtime_match = _RUNTIME_PATTERN.search(message)
            reader = reader_match.group(1) if reader_match else None
            guarded = runtime_match.group(1) if runtime_match else None
            if reader == _RUNTIME_READER and guarded == "True":
                route = "runtime"
            elif reader == _DIRECT_READER and guarded == "False":
                route = "direct"
            else:
                route = None
            if route is None:
                route_unknown += 1
            else:
                route_profiles[f"{route}:{profile}"] += 1
        fields = {}
        for key, accepted in _FIELD.items():
            match = _PATTERN[key].search(message)
            if match and match.group(1) in accepted:
                fields[key] = match.group(1)
        if len(fields) != len(_FIELD):
            counts["verdict_incomplete"] += 1
            continue
        complete += 1
        reasons = [
            key for key, invalid in (
                ("inspection_invalid", fields["inspection_valid"] == "False"),
                ("format_mismatch", fields["inspection_format"] not in ("epub", "pdf")),
                ("protected", fields["inspection_protected"] == "True"),
                ("catalog_invalid", fields["catalog_valid"] == "False"),
            ) if invalid
        ]
        # Format equality to expected_kind is separately required by Core:
        # reject an incomplete observation rather than guessing that format
        # matches merely because both labels belong to {epub,pdf}.
        expected = re.search(r"(?:^| )expected_kind=(epub|pdf)(?: |$)", message)
        if expected is None:
            counts["verdict_incomplete"] += 1
            complete -= 1
            continue
        if fields["inspection_format"] != expected.group(1):
            if "format_mismatch" not in reasons:
                reasons.append("format_mismatch")
        if not reasons:
            counts["inconsistent_without_failing_predicate"] += 1
        for reason in reasons:
            counts[reason] += 1
        if len(reasons) > 1:
            counts["multiple_failed_predicates"] += 1
    return {
        "status": ("no_instrumented_evidence" if matched == 0 else
                   "partial_verdicts" if complete != matched else
                   "instrumented_occurrences"),
        "occurrences": matched,
        "instrumented": complete,
        "reasons": dict(sorted(counts.items())),
        "encryption_profiles": dict(sorted(profiles.items())),
        "route_encryption_profiles": dict(sorted(route_profiles.items())),
        "route_unqualified_occurrences": route_unknown,
        "note": "Occurrences, not distinct items; no causal attribution to historical failures.",
    }



def summarize_document(
    document: object, *, manifest_coverage: object = None,
) -> dict[str, object]:
    """Do not confuse retained-log coverage with evidence of all attempts.

    Even an 'available' day ZIP is a sequential, non-atomic capture of only
    retained rotations. We neither assume earlier logs exist nor infer that
    absence of a logged rejection means success.
    """
    if type(document) is not dict:
        return {"status": "invalid_journal", "occurrences": 0,
                "instrumented": 0, "reasons": {},
                "journal_coverage": "unknown",
                "historical_completeness_attested": False}
    report = classify(document.get("events"))
    # These fields live in the DAILY ZIP manifest, not the raw events JSON.
    # A caller-supplied document containing fake coverage must never be
    # mistaken for validated evidence from the ZIP reader.
    metadata = manifest_coverage if type(manifest_coverage) is dict else {}
    declared = metadata.get("coverage")
    gaps = metadata.get("rotation_gaps_present")
    missing = metadata.get("active_log_missing")
    if (declared == "partial" or gaps is True or missing is True):
        coverage = "partial"
    elif (type(declared) is str and declared in ("available", "empty")
            and gaps is False and missing is False):
        coverage = declared
    else:
        coverage = "unknown"
    report["journal_coverage"] = coverage
    # A stable on-disk log and an available retained window are still not
    # evidence about already rotated-out events or past deliveries.
    report["historical_completeness_attested"] = False
    if report["status"] == "no_instrumented_evidence" and coverage == "partial":
        report["status"] = "no_evidence_partial_coverage"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal_zip", type=Path)
    args = parser.parse_args(argv)
    try:
        data, manifest_coverage = read_raw_journal_zip(
            args.journal_zip, include_manifest_coverage=True,
        )
    except (JournalZipError, OSError):
        # Never print the exception: it may contain a private path.
        print(json.dumps({"status": "journal_unreadable"}))
        return 2
    print(json.dumps(
        summarize_document(data, manifest_coverage=manifest_coverage),
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
