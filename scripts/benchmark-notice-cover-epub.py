#!/usr/bin/env python3
"""Opt-in #2435 N3 benchmark on explicitly selected EPUBs; aggregate output only.

Linux, separate process, read-only input archives, private temporary cache.
No production Storage or provider session is opened. No OS cache eviction.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import resource
import stat
import sys
import tempfile
from time import perf_counter_ns, process_time_ns

MAX_FILE = 2 * 1024**3
MAX_TOTAL = 32 * 1024**3


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def io_counts():
    try:
        return {name: int(value) for name, value in
                (line.split(":", 1) for line in Path("/proc/self/io").read_text().splitlines())
                if name in {"rchar", "wchar", "read_bytes", "write_bytes"}}
    except (OSError, ValueError):
        return {}


def dist(values):
    values = sorted(values)
    if not values:
        return None
    return {"count": len(values),
            "p50_ms": round(values[(len(values)*50+99)//100-1], 4),
            "p95_ms": round(values[(len(values)*95+99)//100-1], 4),
            "max_ms": round(values[-1], 4)}


def measure(paths, cache):
    from cifereca import epub_metadata
    from cifereca.core.book_cover_projection import CoverDocumentScope
    from cifereca.core.epub_cover_materialization import ExactEpubCoverResource, EpubCoverPhysicalCache

    # Both the inspector and restorer use the same isolated cache root.
    if epub_metadata.cover_cache_dir() != cache:
        raise RuntimeError("unexpected_cache")
    prepared = []
    skipped = Counter()
    admitted_bytes = 0
    for number, path in enumerate(paths):
        try:
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE
                        or stamp(os.stat(path, follow_symlinks=False)) != stamp(info)):
                    skipped["invalid_file"] += 1
                    continue
                if admitted_bytes + info.st_size > MAX_TOTAL:
                    skipped["total_budget"] += 1
                    continue
                admitted_bytes += info.st_size
                digest = hashlib.sha256()
                remaining = info.st_size
                while remaining:
                    chunk = stream.read(min(1024**2, remaining))
                    if not chunk:
                        break
                    digest.update(chunk)
                    remaining -= len(chunk)
                if remaining or stream.read(1) or stamp(os.fstat(stream.fileno())) != stamp(info):
                    skipped["unstable"] += 1
                    continue
                epub_metadata._bounded_directory(stream)
                meta = epub_metadata.inspect_epub(stream)
                if (stamp(os.fstat(stream.fileno())) != stamp(info)
                        or stamp(os.stat(path, follow_symlinks=False)) != stamp(info)):
                    skipped["unstable"] += 1
                    continue
            if meta.get("epub_cover_status") != "extracted":
                skipped["no_usable_cover"] += 1
                continue
            cover = meta["epub_cover"]
            sha = digest.hexdigest()
            # Experimental scope only, not a Storage/Placement assertion.
            ref = ExactEpubCoverResource(CoverDocumentScope(
                kind="placement", identity_id="benchmark",
                source_key=f"benchmark:{number}",
                artifact_id="sha256:" + sha, placement_id=f"benchmark:{number}",
            ), 1, sha, cover["member"], cover["cache_name"])
            prepared.append((path, ref, info.st_size))
        except Exception:
            skipped["unqualified"] += 1
    targets = {cache / ref.cache_name for _, ref, _ in prepared}
    for target in targets:
        target.unlink(missing_ok=True)

    report = {"selected": len(paths), "qualified": len(prepared),
              "skipped": dict(skipped), "distinct_cover_media": len(targets),
              "qualified_archive_bytes": sum(size for _, _, size in prepared),
              "workers": 2, "os_cache_flushed": False,
              "physical_io_counters_available": bool(io_counts()),
              "phases": {}}
    loader = EpubCoverPhysicalCache(max_workers=2)
    try:
        for phase in ("n3_cache_cold", "n3_cache_warm"):
            before = loader.stats()
            io_before = io_counts()
            wall, cpu = perf_counter_ns(), process_time_ns()
            durations = []
            failed = 0
            logical_bytes = 0
            for path, ref, size in prepared:
                missing = not (cache / ref.cache_name).exists()
                started = perf_counter_ns()
                result = loader.request(ref, source_path=path).result(timeout=120)
                durations.append((perf_counter_ns() - started) / 1e6)
                if result is None:
                    failed += 1
                elif missing:
                    logical_bytes += size
            cpu_ms = (process_time_ns() - cpu) / 1e6
            wall_ms = (perf_counter_ns() - wall) / 1e6
            io_after = io_counts()
            after = loader.stats()
            report["phases"][phase] = {
                "latency": dist(durations), "wall_ms": round(wall_ms, 3),
                "cpu_ms": round(cpu_ms, 3), "rss_highwater_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "failed": failed, "successful_cold_logical_archive_bytes": logical_bytes,
                "local_cache_bytes": sum(p.stat().st_size for p in targets if p.exists()),
                "io_delta": {key: io_after[key]-io_before[key] for key in io_before.keys() & io_after.keys()},
                "n3_hit": after["n3_epub_hit"]-before["n3_epub_hit"],
                "n3_miss": after["n3_epub_miss"]-before["n3_epub_miss"],
            }
    finally:
        loader.close()
    # The two representative 16/256 baselines are complete-corpus
    # receipts, not best-effort samples. A skipped archive must not turn a
    # 15/16 or 255/256 run into a publishable PASS. Keep the aggregate
    # counters to explain partial runs without disclosing any filename.
    report["complete_manifest"] = len(prepared) == len(paths)
    report["valid"] = (report["complete_manifest"] and bool(prepared)
                       and not any(p["failed"] for p in report["phases"].values()))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="local JSON array of explicitly approved absolute EPUB paths")
    parser.add_argument("--size", type=int, choices=(16, 256), required=True)
    args = parser.parse_args()
    try:
        if sys.platform != "linux":
            raise ValueError()
        with open(args.manifest, "rb") as stream:
            raw = stream.read(1024**2+1)
        if len(raw)>1024**2:
            raise ValueError()
        names = json.loads(raw)
        if (type(names) is not list or len(names)!=args.size
                or any(type(s) is not str or not s or len(s)>4096 for s in names)):
            raise ValueError()
        paths = [Path(s) for s in names]
        if (len(set(paths)) != len(paths)
                or any(not p.is_absolute() or p.suffix.lower() not in (".epub", ".kepub") for p in paths)):
            raise ValueError()
        with tempfile.TemporaryDirectory(prefix="notice-cover-bench-") as scratch:
            os.environ["XDG_CACHE_HOME"] = scratch
            from cifereca import epub_metadata
            cache = epub_metadata.cover_cache_dir()
            if not cache.is_relative_to(Path(scratch)):
                raise RuntimeError()
            report = measure(paths, cache)
        print(json.dumps(report, sort_keys=True))
        return 0 if report["valid"] else 1
    except Exception:
        # No paths, titles, archive members, hashes or raw exceptions.
        print(json.dumps({"valid": False, "error": "benchmark_refused_or_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
