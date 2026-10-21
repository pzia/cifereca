from __future__ import annotations

import hashlib
import io
import math
import multiprocessing as mp
import os
import tempfile
import threading
import time
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .engine import CancelledError, ImageChange, _effective_target_box, _optimize_image
from .profiles import DeviceProfile

ProgressCallback = Callable[[int, str], None]


class PdfOptimizationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PdfInspection:
    source_bytes: int
    pages: int
    images_seen: int
    candidate_images: int
    preserved_images: int
    raster_bytes: int
    estimated_size_bytes: int

    @property
    def estimated_reduction_percent(self) -> float:
        if self.source_bytes <= 0:
            return 0.0
        return max(0.0, (1.0 - self.estimated_size_bytes / self.source_bytes) * 100.0)


@dataclass(slots=True)
class PdfOptimizationReport:
    profile_id: str
    source_path: str
    output_path: str
    source_bytes: int = 0
    output_bytes: int = 0
    source_sha256: str = ""
    output_sha256: str = ""
    pages: int = 0
    images_seen: int = 0
    images_optimized: int = 0
    images_kept: int = 0
    image_notes: list[str] = field(default_factory=list)
    image_errors: list[str] = field(default_factory=list)
    changes: list[ImageChange] = field(default_factory=list)

    @property
    def bytes_saved(self) -> int:
        return max(0, self.source_bytes - self.output_bytes)

    @property
    def reduction_percent(self) -> float:
        if self.source_bytes <= 0:
            return 0.0
        return (1.0 - self.output_bytes / self.source_bytes) * 100.0

    def to_dict(self) -> dict:
        value = asdict(self)
        value["bytes_saved"] = self.bytes_saved
        value["reduction_percent"] = round(self.reduction_percent, 2)
        return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError("traitement annulé")


def _emit(callback: ProgressCallback | None, percent: int, message: str) -> None:
    if callback is not None:
        callback(max(0, min(100, int(percent))), message)


def _pymupdf():
    try:
        import pymupdf
        return pymupdf
    except ImportError as exc:
        raise PdfOptimizationError("PyMuPDF est requis pour optimiser les PDF") from exc


def _open_pdf(source: Path, *, max_pages: int):
    pymupdf = _pymupdf()
    try:
        doc = pymupdf.open(source)
    except Exception as exc:
        raise PdfOptimizationError(f"PDF invalide : {exc}") from exc
    try:
        if bool(getattr(doc, "needs_pass", False)):
            raise PdfOptimizationError("PDF chiffré/protégé : optimisation refusée")
        pages = int(getattr(doc, "page_count", 0) or 0)
        if pages <= 0:
            raise PdfOptimizationError("PDF vide")
        if pages > max_pages:
            raise PdfOptimizationError(f"PDF trop long ({pages} pages > {max_pages})")
        get_sigflags = getattr(doc, "get_sigflags", None)
        # PyMuPDF returns -1 when no signature field exists.
        if callable(get_sigflags) and int(get_sigflags()) > 0:
            raise PdfOptimizationError("PDF signé : optimisation refusée pour préserver la signature")
        return doc
    except Exception:
        doc.close()
        raise


def _image_inventory(doc, *, cancel_event: threading.Event | None = None) -> dict[int, dict]:
    inventory: dict[int, dict] = {}
    for page_index in range(doc.page_count):
        _check_cancel(cancel_event)
        page = doc[page_index]
        page_rect = page.rect
        for row in page.get_images(full=True):
            xref = int(row[0])
            smask = int(row[1] or 0)
            width = int(row[2] or 0)
            height = int(row[3] or 0)
            bpc = int(row[4] or 0)
            colorspace_name = str(row[5] or "")
            item = inventory.setdefault(
                xref,
                {
                    "xref": xref,
                    "smask": smask,
                    "width": width,
                    "height": height,
                    "bpc": bpc,
                    "colorspace_name": colorspace_name,
                    "pages": [],
                    "occurrences": [],
                },
            )
            item["smask"] = max(int(item["smask"]), smask)
            item["bpc"] = min(int(item.get("bpc", bpc) or bpc), bpc) if bpc else int(item.get("bpc", 0) or 0)
            if not item.get("colorspace_name"):
                item["colorspace_name"] = colorspace_name
            item["pages"].append(page_index)
            try:
                rects = page.get_image_rects(xref)
            except Exception:
                rects = []
            for rect in rects:
                item["occurrences"].append(
                    (float(rect.width), float(rect.height), float(page_rect.width), float(page_rect.height))
                )
    return inventory


def _target_box(profile: DeviceProfile, item: dict) -> tuple[int, int]:
    original = (max(1, int(item["width"])), max(1, int(item["height"])))
    base = _effective_target_box(profile, original, None)
    zoom = profile.max_zoom_factor
    if zoom is None or not item.get("occurrences"):
        return base

    screen_width, screen_height = profile.width, profile.height
    if profile.allow_landscape and original[0] > original[1] and screen_height > screen_width:
        screen_width, screen_height = screen_height, screen_width

    wanted_width = wanted_height = 1
    for rect_width, rect_height, page_width, page_height in item["occurrences"]:
        if page_width <= 0 or page_height <= 0:
            continue
        wanted_width = max(
            wanted_width,
            int(math.ceil(screen_width * rect_width / page_width * zoom)),
        )
        wanted_height = max(
            wanted_height,
            int(math.ceil(screen_height * rect_height / page_height * zoom)),
        )
    return min(base[0], wanted_width), min(base[1], wanted_height)


def _suffix_for_extracted(ext: str) -> str | None:
    return {
        "jpeg": ".jpg",
        "jpg": ".jpg",
        "png": ".png",
        "webp": ".webp",
        "tif": ".tif",
        "tiff": ".tiff",
        "bmp": ".bmp",
    }.get(str(ext or "").casefold())


def inspect_pdf(
    input_path: str | os.PathLike[str],
    *,
    profile: DeviceProfile,
    max_pages: int = 5000,
    max_image_pixels: int = 64_000_000,
    max_image_mb: int = 64,
    cancel_event: threading.Event | None = None,
) -> PdfInspection:
    source = Path(input_path).expanduser().resolve()
    if source.suffix.casefold() != ".pdf" or not source.is_file():
        raise PdfOptimizationError("le fichier source doit être un PDF existant")
    _check_cancel(cancel_event)
    doc = _open_pdf(source, max_pages=max_pages)
    with doc:
        inventory = _image_inventory(doc, cancel_event=cancel_event)
        candidate_images = 0
        preserved_images = 0
        raster_bytes = 0
        estimated_delta = 0
        for xref, item in inventory.items():
            _check_cancel(cancel_event)
            if item["smask"] or int(item.get("bpc", 0) or 0) <= 1:
                preserved_images += 1
                continue
            try:
                extracted = doc.extract_image(xref)
            except Exception:
                preserved_images += 1
                continue
            suffix = _suffix_for_extracted(extracted.get("ext", ""))
            data = extracted.get("image", b"")
            raster_bytes += len(data)
            width = int(extracted.get("width", 0) or 0)
            height = int(extracted.get("height", 0) or 0)
            if (
                not suffix
                or not width
                or not height
                or len(data) > max_image_mb * 1024 * 1024
                or width * height > max_image_pixels
            ):
                preserved_images += 1
                continue
            box = _target_box(profile, item)
            resize = width > box[0] or height > box[1]
            tone = bool(profile.gray_levels) or (
                profile.grayscale and int(extracted.get("colorspace", 0) or 0) > 1
            )
            if not (resize or tone):
                continue
            candidate_images += 1
            scale = min(1.0, box[0] / max(1, width), box[1] / max(1, height))
            factor = max(0.25, min(0.95, scale * scale * (0.85 if tone else 0.95)))
            predicted = int(len(data) * factor)
            estimated_delta += max(0, len(data) - predicted)

        source_bytes = source.stat().st_size
        return PdfInspection(
            source_bytes=source_bytes,
            pages=doc.page_count,
            images_seen=len(inventory),
            candidate_images=candidate_images,
            preserved_images=preserved_images,
            raster_bytes=raster_bytes,
            estimated_size_bytes=max(1, min(source_bytes, source_bytes - estimated_delta)),
        )


def _validate_output(
    source: Path,
    output: Path,
    *,
    pages: int,
    max_pages: int,
    cancel_event: threading.Event | None = None,
) -> None:
    _check_cancel(cancel_event)
    before = _open_pdf(source, max_pages=max_pages)
    after = _open_pdf(output, max_pages=max_pages)
    try:
        if after.page_count != pages or before.page_count != after.page_count:
            raise PdfOptimizationError("PDF produit invalide : nombre de pages modifié")
        for index in range(pages):
            _check_cancel(cancel_event)
            if tuple(before[index].rect) != tuple(after[index].rect):
                raise PdfOptimizationError(f"PDF produit invalide : géométrie page {index + 1} modifiée")
            if before[index].get_text("text") != after[index].get_text("text"):
                raise PdfOptimizationError(f"PDF produit invalide : texte page {index + 1} modifié")
            before_drawings = before[index].get_drawings()
            after_drawings = after[index].get_drawings()
            if len(before_drawings) != len(after_drawings):
                raise PdfOptimizationError(f"PDF produit invalide : contenu vectoriel page {index + 1} modifié")
    finally:
        before.close()
        after.close()


# Source bytes and estimated Pillow working set are separate admission budgets.
# The latter is deliberately conservative (several decoded/intermediate copies),
# not an OS-enforced RSS limit. Oversized images keep the sequential path.
_CPU_BATCH_BYTES = 16 * 1024 * 1024
_CPU_BATCH_WORKING_BYTES = 128 * 1024 * 1024
_CPU_WAIT_SECONDS = 120


@dataclass(frozen=True, slots=True)
class _PdfImageJob:
    index: int
    xref: int
    page_index: int
    data: bytes
    name: str
    box: tuple[int, int] | None
    note: str
    error: str


def _pdf_batches(doc, inventory, *, profile, max_image_mb,
                 cpu_workers, cancel_event):
    """Extract only in the coordinator; retain at most N jobs plus one lookahead.

    A job contains only indices, page number, bytes, suffix and a target box.
    No PyMuPDF object is retained in a batch or passed to a child process.
    """
    batch = []
    source_bytes = working_bytes = 0
    for index, (xref, item) in enumerate(inventory.items(), start=1):
        _check_cancel(cancel_event)
        data, name, box, note, error = b"", "", None, "", ""
        if item["smask"]:
            note = "transparence/masque préservé"
        elif int(item.get("bpc", 0) or 0) <= 1:
            note = "masque/stencil 1-bit préservé"
        else:
            try:
                extracted = doc.extract_image(xref)
                data = extracted.get("image", b"")
                suffix = _suffix_for_extracted(extracted.get("ext", ""))
                if not suffix:
                    note = f"format {extracted.get('ext', '?')} conservé"
                elif len(data) > max_image_mb * 1024 * 1024:
                    note = f"image > {max_image_mb} Mio"
                else:
                    name = f"xref-{xref}{suffix}"
                    box = _target_box(profile, item)
                del extracted
            except MemoryError:
                raise PdfOptimizationError("cpu_image_memory_exhausted") from None
            except Exception as exc:
                error = f"extraction impossible ({exc})"
        # Inventory dimensions come from the PDF, before Pillow decoding.
        cost = max(1, item["width"]) * max(1, item["height"]) * 16 + len(data) * 2
        eligible = bool(name and not error and len(data) <= _CPU_BATCH_BYTES
                        and cost <= _CPU_BATCH_WORKING_BYTES)
        if batch and (not eligible or len(batch) >= cpu_workers
                      or source_bytes + len(data) > _CPU_BATCH_BYTES
                      or working_bytes + cost > _CPU_BATCH_WORKING_BYTES):
            yield batch
            batch = []
            source_bytes = working_bytes = 0
        job = _PdfImageJob(index, xref, int(item["pages"][0]), data, name, box, note, error)
        if not eligible:
            yield [job]
            continue
        batch.append(job)
        source_bytes += len(data)
        working_bytes += cost
    if batch:
        yield batch


def _run_pdf_batches(doc, inventory, report, *, profile, max_image_mb,
                     max_image_pixels, cpu_workers, progress, cancel_event):
    with ExitStack() as stack:
        pool = None
        unavailable = False
        batches = _pdf_batches(
            doc, inventory, profile=profile, max_image_mb=max_image_mb,
            cpu_workers=cpu_workers, cancel_event=cancel_event,
        )
        # Closing the generator promptly releases lookahead and pending bytes
        # even if a callback or PDF mutation raises in the coordinator.
        stack.callback(batches.close)
        for batch in batches:
            _check_cancel(cancel_event)
            # Do not pay spawn costs for single-image/small documents or for
            # images that exceed the aggregate decoded working-set allowance.
            parallel = cpu_workers > 1 and len(batch) > 1 and not unavailable
            if parallel and pool is None:
                try:
                    pool = stack.enter_context(mp.get_context("spawn").Pool(
                        processes=cpu_workers, maxtasksperchild=64,
                    ))
                except (OSError, RuntimeError):
                    _check_cancel(cancel_event)
                    unavailable = True
                    parallel = False
                    report.image_notes.append(
                        "Processus CPU indisponibles : repli séquentiel sécurisé."
                    )
            pending = []
            if parallel:
                # Keep this generation before submission: Pool can replace a
                # child as soon as a submitted task kills it.
                children = tuple(pool._pool)
                try:
                    for job in batch:
                        _check_cancel(cancel_event)
                        result = pool.apply_async(
                            _optimize_image, args=(job.data, job.name, profile),
                            kwds={"target_box": job.box,
                                  "max_image_pixels": max_image_pixels},
                        )
                        pending.append((result, time.monotonic() + _CPU_WAIT_SECONDS))
                except (CancelledError, KeyboardInterrupt):
                    raise
                except Exception:
                    raise PdfOptimizationError("cpu_image_worker_failed") from None
            for offset, job in enumerate(batch):
                index, xref = job.index, job.xref
                page_index, data, name = job.page_index, job.data, job.name
                box, note, error = job.box, job.note, job.error
                _check_cancel(cancel_event)
                if note or error:
                    report.images_kept += 1
                    if note:
                        report.image_notes.append(f"xref {xref}: {note}")
                    else:
                        report.image_errors.append(f"xref {xref}: {error}")
                else:
                    transformed = None
                    if parallel:
                        result, deadline = pending[offset]
                        while transformed is None:
                            _check_cancel(cancel_event)
                            if any(child.exitcode not in (None, 0) for child in children):
                                raise PdfOptimizationError("cpu_image_worker_failed")
                            left = deadline - time.monotonic()
                            if left <= 0:
                                raise PdfOptimizationError("cpu_image_worker_timeout")
                            try:
                                transformed = result.get(timeout=min(0.2, left))
                            except mp.TimeoutError:
                                continue
                            except (EOFError, ConnectionError, MemoryError):
                                raise PdfOptimizationError("cpu_image_worker_failed") from None
                            except Exception as exc:
                                transformed = exc
                    else:
                        try:
                            transformed = _optimize_image(
                                data, name, profile, target_box=box,
                                max_image_pixels=max_image_pixels,
                            )
                        except MemoryError:
                            raise PdfOptimizationError("cpu_image_memory_exhausted") from None
                        except Exception as exc:
                            transformed = exc
                    if isinstance(transformed, Exception):
                        report.images_kept += 1
                        report.image_errors.append(f"xref {xref}: {transformed}")
                    else:
                        optimized, change = transformed
                        report.changes.append(change)
                        if optimized == data:
                            report.images_kept += 1
                            if "conservée (" in change.action:
                                report.image_notes.append(f"xref {xref}: {change.action}")
                        else:
                            try:
                                doc[page_index].replace_image(xref, stream=optimized)
                                report.images_optimized += 1
                            except MemoryError:
                                raise PdfOptimizationError("cpu_image_memory_exhausted") from None
                            except Exception as exc:
                                report.images_kept += 1
                                report.image_errors.append(f"xref {xref}: {exc}")
                pct = 5 + int(86 * index / max(1, len(inventory)))
                _emit(progress, pct, f"Image PDF {index}/{len(inventory)}")


def optimize_pdf(
    input_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    profile: DeviceProfile,
    max_pages: int = 5000,
    max_image_pixels: int = 64_000_000,
    max_image_mb: int = 64,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    cpu_workers: int = 1,
) -> PdfOptimizationReport:
    if type(cpu_workers) is not int or cpu_workers not in (1, 2, 4):
        raise PdfOptimizationError("cpu_workers doit valoir 1, 2 ou 4")
    source = Path(input_path).expanduser().resolve()
    target = Path(output_path).expanduser().resolve()
    if source == target:
        raise PdfOptimizationError("la sortie doit être différente du PDF original")
    if source.suffix.casefold() != ".pdf" or not source.is_file():
        raise PdfOptimizationError("le fichier source doit être un PDF existant")
    target.parent.mkdir(parents=True, exist_ok=True)

    report = PdfOptimizationReport(
        profile_id=profile.id,
        source_path=str(source),
        output_path=str(target),
        source_bytes=source.stat().st_size,
        source_sha256=_sha256(source),
    )
    _emit(progress, 1, "Validation du PDF…")
    _check_cancel(cancel_event)

    doc = _open_pdf(source, max_pages=max_pages)
    temp_name: str | None = None
    try:
        with doc:
            report.pages = doc.page_count
            inventory = _image_inventory(doc, cancel_event=cancel_event)
            report.images_seen = len(inventory)
            _emit(progress, 5, f"{len(inventory)} image(s) PDF détectée(s)")
            available_cpus = os.cpu_count() or 1
            effective_workers = min(cpu_workers, available_cpus)
            if effective_workers != cpu_workers:
                report.image_notes.append(
                    f"Processus CPU limités à {effective_workers} (CPU disponibles)."
                )
            _run_pdf_batches(
                doc, inventory, report, profile=profile,
                max_image_mb=max_image_mb, max_image_pixels=max_image_pixels,
                cpu_workers=effective_workers, progress=progress, cancel_event=cancel_event,
            )

            _check_cancel(cancel_event)
            _emit(progress, 93, "Réécriture conservatrice du PDF…")
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp.pdf",
                dir=str(target.parent),
            )
            os.close(fd)
            Path(temp_name).unlink(missing_ok=True)
            try:
                doc.save(temp_name, garbage=3, deflate=True, deflate_images=True)
            except Exception as exc:
                raise PdfOptimizationError(f"écriture PDF impossible : {exc}") from exc

        temp_path = Path(temp_name)
        _emit(progress, 97, "Validation de la sortie…")
        _validate_output(
            source,
            temp_path,
            pages=report.pages,
            max_pages=max_pages,
            cancel_event=cancel_event,
        )
        _check_cancel(cancel_event)
        os.replace(temp_path, target)
        temp_name = None
        report.output_bytes = target.stat().st_size
        report.output_sha256 = _sha256(target)
        _emit(progress, 100, "PDF optimisé")
        return report
    except (CancelledError, PdfOptimizationError):
        raise
    except Exception as exc:
        raise PdfOptimizationError(str(exc)) from exc
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


__all__ = [
    "PdfInspection",
    "PdfOptimizationError",
    "PdfOptimizationReport",
    "inspect_pdf",
    "optimize_pdf",
]
