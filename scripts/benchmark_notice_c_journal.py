"""#4982 offline journal microbenchmark, ONLY on a scratch SQLite database.

Run PYTHONPATH=src python scripts/benchmark_notice_c_journal.py
No provider, UI, config, consent, HTTP, application DB or timing SLA.
Optional ports are measured only when actually installed in this checkout.
Synthetic completion measures the journal, NOT the canonical C writer.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.util
from inspect import signature
import json
from pathlib import Path
import statistics
from tempfile import TemporaryDirectory
from time import perf_counter_ns

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
from cifereca.core.notice_cover_projection import notice_cover_document_ref
from cifereca.core.notice_source_observation import NoticeSourceKey
from cifereca.storage import Storage

KINDS = ("fetch_source_notice", "reparse_cached_input",
         "relink_notices", "recompute_aggregate")
NOW = 100
IMPLEMENTATION = sha256(b"benchmark-only").hexdigest()


def _digest(value):
    return sha256(value.encode("ascii")).hexdigest()


def _spec(index, kind, deps):
    if kind in KINDS[:2]:
        document = notice_cover_document_ref(NoticeSourceKey(
            provider_id="benchmark", visibility="public", identity_id=None,
            credential_id=None, account_id=None, branch_id="catalogue",
            record_id=f"record:{index}", edition_id=f"edition:{index}",
            manifestation_role="digital",
        ))
    else:
        document = canonical_cover_document_ref(f"edition:benchmark:{index}")
    return journal.public_job_spec(
        kind, document, input_digest=_digest(str(index)),
        implementation_digest=IMPLEMENTATION, dependencies=deps,
    )


def _distribution(samples):
    ordered = sorted(samples)
    return {
        "count": len(ordered),
        "p50_us": round(statistics.median(ordered), 3),
        "p95_us": round(ordered[(95 * len(ordered) + 99)//100 - 1], 3),
        "max_us": round(ordered[-1], 3),
    }


def benchmark(*, chains=512, samples=64):
    if (type(chains) is not int or not 1 <= chains <= 512
            or type(samples) is not int or not 1 <= samples <= 128):
        raise ValueError("benchmark_sample_budget_invalid")
    durations = {}
    counts = {}
    def measure(name, operation):
        start = perf_counter_ns()
        result = operation()
        durations.setdefault(name, []).append((perf_counter_ns()-start)/1000)
        return result

    with TemporaryDirectory(prefix="notice-c-benchmark-") as scratch:
        db = Storage(Path(scratch) / "benchmark.sqlite")
        try:
            groups = []
            for index in range(chains):
                refs = []
                for kind in KINDS:
                    spec = _spec(index, kind, tuple(refs[-1:]))
                    ref = measure("request_new", lambda: journal.request_job(
                        db, spec, now=NOW, expected_generation=0,
                    ))
                    same = measure("request_dedup", lambda: journal.request_job(
                        db, spec, now=NOW, expected_generation=0,
                    ))
                    assert ref == same
                    refs.append(ref)
                groups.append(tuple(refs))

            # Bounded scan of actual pending entries before completion.
            # No successful result is claimed for a port absent on main.
            read_page = getattr(journal, "read_ready_page", None)
            counts["scan_port_available"] = callable(read_page)
            counts["filtered_scan_port_available"] = (
                callable(read_page) and "supported" in signature(read_page).parameters
            )
            if callable(read_page):
                after = None
                cursors = dict.fromkeys(KINDS)
                counts["scan_examined"] = counts["rotation_examined"] = 0
                for _ in range(samples):
                    page = measure("ready_page32", lambda: read_page(
                        db, now=NOW, after=after, limit=32,
                    ))
                    after = page.next_after
                    assert page.examined <= 32 and page.corrupt == 0
                    counts["scan_examined"] += page.examined

                    def four_pages():
                        examined = 0
                        for kind in KINDS:
                            result = read_page(
                                db, now=NOW, after=cursors[kind], limit=8,
                                supported=((kind, IMPLEMENTATION),),
                            )
                            cursors[kind] = result.next_after
                            assert result.corrupt == 0
                            examined += result.examined
                        return examined

                    if counts["filtered_scan_port_available"]:
                        examined = measure("four_filtered_pages8", four_pages)
                        assert examined <= 32
                        counts["rotation_examined"] += examined

            def finish(lease):
                with db._lock, db._connect() as sql:
                    sql.execute("BEGIN IMMEDIATE")
                    journal._complete_job_txn(sql, lease, now=NOW)

            for refs in groups:
                for rank, ref in enumerate(refs):
                    kind = KINDS[rank]
                    lease = measure("claim_"+kind, lambda: journal.claim_job(
                        db, ref, now=NOW, lease_seconds=30,
                    ))
                    assert lease is not None
                    measure("complete_"+kind, lambda: finish(lease))
                    job = measure("read_"+kind, lambda: journal.read_job(db, ref))
                    assert job.state == "current"
            assert db.entitlements() == [] and db.acquisitions() == []

            retention_module = "cifereca.core.notice_c_job_retention"
            counts["retention_port_available"] = importlib.util.find_spec(retention_module) is not None
            if counts["retention_port_available"]:
                from cifereca.core.notice_c_job_retention import recycle_terminal_jobs
                counts["recycled"] = counts["retention_examined"] = 0
                for _ in range(min(samples, 8)):
                    result = measure("recycle32", lambda: recycle_terminal_jobs(
                        db, now=NOW + 86400 + journal._MAX_LEASE, limit=32,
                    ))
                    counts["recycled"] += result.recycled
                    counts["retention_examined"] += result.examined
                    assert result.recycled <= 32
                    if result.recycled == 0:
                        break
            return {
                "measurement_origin": "synthetic_scratch_sqlite",
                "chains": chains, "initial_jobs": chains*4,
                "sample_budget": samples, "counts": counts,
                "latency": {name: _distribution(series)
                            for name, series in sorted(durations.items())},
            }
        finally:
            db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chains", type=int, default=512)
    parser.add_argument("--samples", type=int, default=64)
    args = parser.parse_args()
    print(json.dumps(benchmark(chains=args.chains, samples=args.samples),
                     ensure_ascii=True, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
