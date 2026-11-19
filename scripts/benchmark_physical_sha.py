"""#6936: bounded read-only physical SHA microbenchmark (Linux).

PYTHONPATH=src python scripts/benchmark_physical_sha.py
Only deterministic scratch files, no app Storage/Config, provider, rights,
or production writer. Compare the SAME guarded reader serial vs 2/4 distinct
files concurrently, never cache hashes or skip revalidation. Warm filesystem
cache by design; read_bytes is kernel physical I/O, rchar logical read I/O.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import resource
import statistics
import sys
from tempfile import TemporaryDirectory
from time import perf_counter_ns, process_time_ns

from cifereca.catalog import LocalCatalog
from cifereca.core.derived_variant_publication import _verified_variant_file

_MIB = 1024 * 1024
_MAX_CORPUS = 512 * _MIB


def _distribution(values):
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "p50_ms": round(statistics.median(ordered) / 1e6, 3),
        "p95_ms": round(ordered[(95 * len(ordered) + 99)//100 - 1] / 1e6, 3),
        "max_ms": round(ordered[-1] / 1e6, 3),
    }


def _io():
    try:
        data = dict(line.strip().split(": ", 1)
                    for line in Path("/proc/self/io").read_text().splitlines())
        return {name: int(data[name]) for name in ("rchar", "read_bytes")}
    except (OSError, ValueError, KeyError):
        return None


def _rss():
    # ru_maxrss Linux is KiB and process cumulative, not a per-reader peak.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def _corpus(root, files, size):
    """Distinct deterministic bytes, no sparse files or user input paths."""
    jobs = []
    for i in range(files):
        target = root / f"synthetic-{i:04d}.epub"
        digest = hashlib.sha256()
        written = 0
        with target.open("xb") as stream:
            while written < size:
                count = min(_MIB, size - written)
                block = hashlib.shake_256(f"sha-benchmark:{i}:{written}".encode()).digest(count)
                stream.write(block)
                digest.update(block)
                written += count
            stream.flush()
            os.fsync(stream.fileno())
        jobs.append((target, digest.hexdigest(), size))
    return tuple(jobs)


def _read(job, mode):
    path, digest, size = job
    started = perf_counter_ns()
    if mode == "catalog":
        if LocalCatalog.sha256(path) != digest:
            raise RuntimeError("benchmark_hash_mismatch")
    elif mode == "pinned":
        with _verified_variant_file(path, digest, expected_size=size) as check:
            check()  # explicit final named-object recheck, like a caller
    else:
        raise ValueError("benchmark_reader_invalid")
    return perf_counter_ns() - started


def _one_case(jobs, *, mode, workers, passes):
    before_io = _io()
    cpu = process_time_ns()
    wall = perf_counter_ns()
    latencies, batch_ns = [], []
    for _ in range(passes):
        start = perf_counter_ns()
        if workers == 1:
            latencies.extend(_read(job, mode) for job in jobs)
        else:
            # At most four concurrent distinct, immutable scratch files.
            # No parallelization of active production transactions.
            with ThreadPoolExecutor(max_workers=workers) as pool:
                latencies.extend(pool.map(lambda job: _read(job, mode), jobs))
        batch_ns.append(perf_counter_ns() - start)
    elapsed = perf_counter_ns() - wall
    cpu_ns = process_time_ns() - cpu
    after_io = _io()
    io_delta = None if before_io is None or after_io is None else {
        key: max(0, after_io[key] - before_io[key]) for key in before_io}
    logical = sum(job[2] for job in jobs) * passes
    return {
        "reader": mode, "workers": workers, "passes": passes,
        "files_checked": len(latencies), "logical_bytes_hashed": logical,
        "file_latency": _distribution(latencies),
        "batch_latency": _distribution(batch_ns),
        "wall_ms": round(elapsed/1e6, 3), "cpu_ms": round(cpu_ns/1e6, 3),
        "throughput_mib_s": round(logical/_MIB/(elapsed/1e9), 3),
        "process_peak_rss_kib_cumulative": _rss(),
        "proc_io_delta": io_delta,
    }


def benchmark(*, files=32, size_mib=4, passes=5):
    if (type(files) is not int or not 2 <= files <= 512
            or type(size_mib) is not int or not 1 <= size_mib <= 128
            or files * size_mib * _MIB > _MAX_CORPUS
            or type(passes) is not int or not 1 <= passes <= 12):
        raise ValueError("benchmark_budget_invalid")
    with TemporaryDirectory(prefix="cifereca-physical-sha-") as scratch:
        jobs = _corpus(Path(scratch), files, size_mib*_MIB)
        baseline = []
        # Warm filesystem cache without retaining an authoritative digest.
        for job in jobs:
            _read(job, "pinned")
        baseline.append(_one_case(jobs, mode="catalog", workers=1, passes=passes))
        baseline.append(_one_case(jobs, mode="pinned", workers=1, passes=passes))
        candidates = [_one_case(jobs, mode="pinned", workers=workers, passes=passes)
                      for workers in (2, 4)]
        # Recheck all bytes after every candidate campaign. No result is
        # carried into a provider or database decision.
        for job in jobs:
            _read(job, "pinned")
    return {
        "measurement_origin": "synthetic_scratch_warm_cache",
        "files": files, "file_size_mib": size_mib, "passes": passes,
        "python": sys.version.split()[0], "cpu_count": os.cpu_count(),
        "baseline": baseline, "candidates": candidates,
        "production_optimization_applied": False,
        "cold_disk_measured": False,
        "rights_or_placement_authorized": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=32)
    parser.add_argument("--size-mib", type=int, default=4)
    parser.add_argument("--passes", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(benchmark(files=args.files, size_mib=args.size_mib,
                               passes=args.passes), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
