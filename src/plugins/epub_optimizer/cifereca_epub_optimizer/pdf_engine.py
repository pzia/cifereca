from __future__ import annotations

import hashlib
import io
import math
import os
import tempfile
import threading
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
) -> PdfOptimizationReport:
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
            # Keep no list of extracted image byte blobs. Large PDFs can
            # contain hundreds of individually legal images; retaining all
            # at once bypassed per-image memory limits.
            total_images = max(1, len(inventory))
            _emit(progress, 5, f"{len(inventory)} image(s) PDF détectée(s)")

            for index, (xref, item) in enumerate(inventory.items(), start=1):
                _check_cancel(cancel_event)
                try:
                    extracted = doc.extract_image(xref)
                    data = extracted.get("image", b"")
                    suffix = _suffix_for_extracted(extracted.get("ext", "")) or ""
                except Exception as exc:
                    report.images_kept += 1
                    report.image_errors.append(
                        f"xref {xref}: extraction impossible ({exc})"
                    )
                else:
                    if item["smask"]:
                        report.images_kept += 1
                        report.image_notes.append(f"xref {xref}: transparence/masque préservé")
                    elif int(item.get("bpc", 0) or 0) <= 1:
                        report.images_kept += 1
                        report.image_notes.append(f"xref {xref}: masque/stencil 1-bit préservé")
                    elif not suffix:
                        report.images_kept += 1
                        report.image_notes.append(
                            f"xref {xref}: format {extracted.get('ext', '?')} conservé"
                        )
                    elif len(data) > max_image_mb * 1024 * 1024:
                        report.images_kept += 1
                        report.image_notes.append(f"xref {xref}: image > {max_image_mb} Mio")
                    else:
                        try:
                            box = _target_box(profile, item)
                            optimized, change = _optimize_image(
                                data,
                                f"xref-{xref}{suffix}",
                                profile,
                                target_box=box,
                                max_image_pixels=max_image_pixels,
                            )
                            report.changes.append(change)
                            if optimized == data:
                                report.images_kept += 1
                                if "conservée (" in change.action:
                                    report.image_notes.append(f"xref {xref}: {change.action}")
                            else:
                                page_index = int(item["pages"][0])
                                doc[page_index].replace_image(xref, stream=optimized)
                                report.images_optimized += 1
                        except Exception as exc:
                            report.images_kept += 1
                            report.image_errors.append(f"xref {xref}: {exc}")

                # Counts replace byte-weighted progress: no second extraction
                # pass or in-memory byte inventory required for a percentage.
                pct = 5 + int(86 * index / total_images)
                _emit(progress, pct, f"Image PDF {index}/{len(inventory)}")

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
