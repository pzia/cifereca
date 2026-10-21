"""Explicit synthetic headless benchmark for #2432 / #1741 (no GUI/network).

Each measured transform runs in a fresh coordinator, including spawn, image
transforms, conservative validation, hashing and publication. Fixture creation
and JSON serialization are outside the timer. This is a test harness, not a
second in-product diagnostics collector. RSS uses Linux getrusage peak values:
coordinator and largest reaped child, NOT aggregate concurrent RSS.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile

# Permit direct invocation from a source checkout and spawn imports.
sys.path.insert(0, os.environ.get("CIFERECA_BENCH_PACKAGE_ROOT", str(Path(__file__).resolve().parents[1])))
from PIL import Image
import PIL
import pymupdf
from cifereca_epub_optimizer.builtin_profiles import builtin_profile
from cifereca_epub_optimizer.engine import optimize_epub
from cifereca_epub_optimizer.pdf_engine import optimize_pdf


def fixtures(root, *, heavy_images, width, height):
    rng = random.Random(2432)
    for case in ("small", "heavy"):
        images = []
        count = 1 if case == "small" else heavy_images
        size = (160, 220) if case == "small" else (width, height)
        for _ in range(count):
            image = Image.frombytes("RGB", size, rng.randbytes(size[0] * size[1] * 3))
            out = io.BytesIO()
            image.save(out, "JPEG", quality=92)
            images.append(out.getvalue())
        with pymupdf.open() as doc:
            for image in images:
                page = doc.new_page(width=400, height=600)
                page.insert_text((20, 20), "Synthetic benchmark")
                page.insert_image(pymupdf.Rect(20, 30, 380, 580), stream=image)
            doc.save(root / f"{case}.pdf")
        with zipfile.ZipFile(root / f"{case}.epub", "w") as archive:
            archive.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>')
            items, spine = [], []
            for index, image in enumerate(images):
                items += [f'<item id="i{index}" href="{index}.jpg" media-type="image/jpeg"/>',
                          f'<item id="p{index}" href="{index}.xhtml" media-type="application/xhtml+xml"/>']
                spine.append(f'<itemref idref="p{index}"/>')
                archive.writestr(f"{index}.jpg", image)
                archive.writestr(f"{index}.xhtml", f'<html xmlns="http://www.w3.org/1999/xhtml"><body><img src="{index}.jpg"/></body></html>')
            archive.writestr("content.opf", '<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata/><manifest>' + ''.join(items) + '</manifest><spine>' + ''.join(spine) + '</spine></package>')


def semantic_digest(path):
    digest = hashlib.sha256()
    if path.suffix == ".epub":
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                digest.update(name.encode())
                digest.update(archive.read(name))
    else:
        with pymupdf.open(path) as doc:
            for page in doc:
                digest.update(repr(tuple(page.rect)).encode())
                digest.update(page.get_text().encode())
                for row in page.get_images(full=True):
                    digest.update(doc.extract_image(row[0])["image"])
    return digest.hexdigest()


def sample(source, workers):
    target = source.with_name("out" + source.suffix)
    before = resource.getrusage(resource.RUSAGE_SELF)
    start = time.perf_counter()
    function = optimize_pdf if source.suffix == ".pdf" else optimize_epub
    options = {"cpu_workers": workers} if workers != 1 else {}
    report = function(source, target, profile=builtin_profile("xteink-x3"), **options)
    elapsed_ms = (time.perf_counter() - start) * 1000
    after = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "wall_ms": elapsed_ms,
        "cpu_ms": ((after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime)
                   + children.ru_utime + children.ru_stime) * 1000,
        "coordinator_peak_rss_kib": after.ru_maxrss,
        "largest_child_peak_rss_kib": children.ru_maxrss,
        "source_bytes": report.source_bytes, "output_bytes": report.output_bytes,
        "semantic_sha256": semantic_digest(target),
        "image_errors": len(report.image_errors), "notes": report.image_notes,
    }


def distribution(values):
    ordered = sorted(values)
    return {"count": len(values), "p50": statistics.median(values),
            "p95": ordered[math.ceil(.95 * len(values)) - 1],
            "min": ordered[0], "max": ordered[-1],
            "stddev": statistics.pstdev(values)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--heavy-images", type=int, default=12)
    parser.add_argument("--width", type=int, default=1000)
    parser.add_argument("--height", type=int, default=1500)
    parser.add_argument("--baseline-root", type=Path)
    parser.add_argument("--baseline-ref", default="unspecified")
    parser.add_argument("--sample", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--workers", type=int, choices=(1, 2, 4), default=1, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.sample:
        print(json.dumps(sample(args.sample, args.workers)))
        return
    if not (5 <= args.repetitions <= 40 and 2 <= args.heavy_images <= 24
            and 64 <= args.width <= 2000 and 64 <= args.height <= 3000):
        parser.error("synthetic sample budget exceeded")
    cases = [f"{case}.{suffix}" for case in ("small", "heavy") for suffix in ("epub", "pdf")]
    modes = ("baseline", 1, 2, 4) if args.baseline_root else (1, 2, 4)
    observations = {case: {workers: [] for workers in modes} for case in cases}
    with tempfile.TemporaryDirectory(prefix="cifereca-cpu-benchmark-") as td:
        root = Path(td)
        fixtures(root, heavy_images=args.heavy_images, width=args.width, height=args.height)
        for block in range(args.repetitions + 1):
            # Warmup excluded; rotate modes and case order to reduce ordering bias.
            order = modes
            offset = block % len(modes)
            for case in cases if block % 2 == 0 else reversed(cases):
                for workers in order[offset:] + order[:offset]:
                    sample_env = dict(os.environ)
                    if workers == "baseline":
                        sample_env["CIFERECA_BENCH_PACKAGE_ROOT"] = str(args.baseline_root.resolve())
                    process = subprocess.run(
                        [sys.executable, __file__, "--sample", str(root / case), "--workers", str(1 if workers == "baseline" else workers)],
                        env=sample_env,
                        capture_output=True, text=True, timeout=240, check=True,
                    )
                    observed = json.loads(process.stdout)
                    if observed["image_errors"]:
                        raise RuntimeError("benchmark_image_error")
                    if block:
                        observations[case][workers].append(observed)
    summaries = {}
    for case, modes in observations.items():
        signatures = {s["semantic_sha256"] for samples in modes.values() for s in samples}
        if len(signatures) != 1:
            raise RuntimeError("benchmark_semantic_mismatch")
        summaries[case] = {
            workers: {"metrics": {key: distribution([s[key] for s in samples])
                                 for key in ("wall_ms", "cpu_ms", "coordinator_peak_rss_kib", "largest_child_peak_rss_kib")},
                      "source_bytes": samples[0]["source_bytes"],
                      "output_bytes": distribution([s["output_bytes"] for s in samples]),
                      "samples": samples} for workers, samples in modes.items()
        }
    print(json.dumps({
        "schema": "cifereca.optimizer.cpu-benchmark/1", "measurement_origin": "synthetic_headless",
        "python": platform.python_version(), "pillow": PIL.__version__, "pymupdf": pymupdf.__version__,
        "platform": platform.system(), "cpu_count": os.cpu_count(),
        "protocol": {"repetitions": args.repetitions, "warmup_blocks_excluded": 1,
                     "order": "rotating", "percentile_method": "nearest_rank",
                     "fresh_coordinator_per_sample": True, "spawn_included": True,
                     "heavy_images": args.heavy_images, "heavy_dimensions": [args.width, args.height],
                     "baseline_ref": args.baseline_ref if args.baseline_root else None},
        "limits": {"aggregate_rss_measured": False, "gtk_latency_measured": False,
                   "coordinator_import_cpu_excluded": True, "child_startup_cpu_included": True,
                   "rss_unit": "KiB (Linux getrusage)", "rss_is_peak_not_p95_time_series": True,
                   "small_sample_p95_is_exploratory": args.repetitions < 20,
                   "network_used": False, "private_data_used": False},
        "performance_verdict": "not_qualified", "cases": summaries,
    }, indent=2))


if __name__ == "__main__":
    main()
