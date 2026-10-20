from __future__ import annotations

import hashlib
import io
import multiprocessing as mp
import os
import posixpath
import re
import stat
import tempfile
import threading
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable
from xml.etree import ElementTree as ET

from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError

from .profiles import DeviceProfile

ProgressCallback = Callable[[int, str], None]


class EpubOptimizationError(RuntimeError):
    pass


class DrmProtectedError(EpubOptimizationError):
    pass


class CancelledError(EpubOptimizationError):
    pass


@dataclass(slots=True)
class ImageChange:
    path: str
    original_bytes: int
    optimized_bytes: int
    original_width: int
    original_height: int
    optimized_width: int
    optimized_height: int
    original_mode: str
    optimized_mode: str
    action: str


@dataclass(slots=True)
class OptimizationReport:
    profile_id: str
    source_path: str
    output_path: str
    source_bytes: int = 0
    output_bytes: int = 0
    source_sha256: str = ""
    output_sha256: str = ""
    images_seen: int = 0
    images_optimized: int = 0
    images_kept: int = 0
    image_errors: list[str] = field(default_factory=list)
    image_notes: list[str] = field(default_factory=list)
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
        data = asdict(self)
        data["bytes_saved"] = self.bytes_saved
        data["reduction_percent"] = round(self.reduction_percent, 2)
        return data


@dataclass(slots=True)
class EpubAnalysis:
    source_bytes: int
    estimated_size_bytes: int
    images_seen: int
    images_candidates: int
    images_semantic_preserved: int
    images_over_limit: int
    layout_hints: int


_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
_FONT_ONLY_ENCRYPTION = {
    "http://www.idpf.org/2008/embedding",
    "http://ns.adobe.com/pdf/enc#RC",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError("traitement annulé")


def _emit(callback: ProgressCallback | None, percent: int, message: str) -> None:
    if callback is not None:
        callback(max(0, min(100, int(percent))), message)


def _safe_entry_name(name: str) -> bool:
    if "\\" in name or "\x00" in name:
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts


def _canonical_entry_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if not _safe_entry_name(name):
        return ""
    normalized = posixpath.normpath(name)
    if normalized in {"", "."} or normalized.startswith("../") or normalized.startswith("/"):
        return ""
    canonical = normalized + "/" if info.is_dir() else normalized
    return canonical if canonical == name else ""


def _unsafe_zip_entry(info: zipfile.ZipInfo) -> str:
    if not _canonical_entry_name(info):
        return "chemin non canonique ou dangereux"
    if info.flag_bits & 0x1:
        return "entrée ZIP chiffrée"
    if info.create_system == 3:
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            return "lien symbolique ZIP"
    return ""


def _validate_archive_structure(
    archive: zipfile.ZipFile,
    infos: list[zipfile.ZipInfo],
    *,
    max_uncompressed_mb: int,
) -> str:
    if not infos:
        raise EpubOptimizationError("EPUB vide")

    total_uncompressed = sum(max(0, int(info.file_size)) for info in infos)
    max_uncompressed = max(1, int(max_uncompressed_mb)) * 1024 * 1024
    if total_uncompressed > max_uncompressed:
        raise EpubOptimizationError(
            f"EPUB décompressé trop volumineux ({total_uncompressed / 1024 / 1024:.0f} Mio > {max_uncompressed_mb} Mio)"
        )

    canonical_names: list[str] = []
    for info in infos:
        reason = _unsafe_zip_entry(info)
        if reason:
            raise EpubOptimizationError(f"entrée ZIP refusée ({reason}) : {info.filename}")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise EpubOptimizationError(
                f"entrée ZIP refusée (compression OCF non supportée) : {info.filename}"
            )
        canonical_names.append(info.filename.casefold())
    if len(canonical_names) != len(set(canonical_names)):
        raise EpubOptimizationError("EPUB invalide : chemins ZIP dupliqués")

    mimetype_info = infos[0]
    if mimetype_info.filename != "mimetype":
        raise EpubOptimizationError("EPUB invalide : mimetype doit être la première entrée")
    if (
        mimetype_info.is_dir()
        or mimetype_info.compress_type != zipfile.ZIP_STORED
        or bool(mimetype_info.extra)
        or mimetype_info.file_size != len(b"application/epub+zip")
    ):
        raise EpubOptimizationError("mimetype EPUB invalide")
    if archive.read(mimetype_info) != b"application/epub+zip":
        raise EpubOptimizationError("mimetype EPUB invalide")
    return "mimetype"


def _find_case_insensitive(names: list[str], wanted: str) -> str | None:
    lower = wanted.lower()
    for name in names:
        if name.lower() == lower:
            return name
    return None


_CSS_PX_RE = re.compile(r"^\\s*(\\d+(?:\\.\\d+)?)\\s*(?:px)?\\s*$", re.IGNORECASE)
_STYLE_SIZE_RE = re.compile(r"(?:^|;)\\s*(width|height)\\s*:\\s*(\\d+(?:\\.\\d+)?)px\\s*(?:;|$)", re.IGNORECASE)


def _css_px(value: str | None) -> int | None:
    if not value:
        return None
    match = _CSS_PX_RE.match(str(value))
    if not match:
        return None
    parsed = int(round(float(match.group(1))))
    return parsed if parsed > 0 else None


def _layout_hints(archive: zipfile.ZipFile, infos: list[zipfile.ZipInfo]) -> dict[str, tuple[int | None, int | None]]:
    """Extract conservative rendered-size hints from XHTML img attributes/inline CSS.

    External stylesheet cascade is deliberately not guessed. Hints are only
    actionable for profiles that explicitly publish max_zoom_factor.
    """
    names = {info.filename for info in infos}
    hints: dict[str, tuple[int | None, int | None]] = {}
    for info in infos:
        suffix = Path(info.filename).suffix.lower()
        if suffix not in {".xhtml", ".html", ".htm"} or info.file_size > 8 * 1024 * 1024:
            continue
        try:
            root = ET.fromstring(archive.read(info))
        except Exception:
            continue
        base = posixpath.dirname(info.filename)
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1].lower() != "img":
                continue
            src = str(node.attrib.get("src") or "").split("#", 1)[0].split("?", 1)[0].strip()
            if not src or "://" in src or src.startswith("data:"):
                continue
            resolved = posixpath.normpath(posixpath.join(base, src))
            if resolved.startswith("../") or resolved not in names:
                continue
            width = _css_px(node.attrib.get("width"))
            height = _css_px(node.attrib.get("height"))
            style = str(node.attrib.get("style") or "")
            for axis, value in _STYLE_SIZE_RE.findall(style):
                if axis.lower() == "width":
                    width = int(round(float(value)))
                else:
                    height = int(round(float(value)))
            if width or height:
                previous = hints.get(resolved)
                if previous is None:
                    hints[resolved] = (width, height)
                else:
                    # Keep the largest declared rendering: it is the least
                    # destructive choice when the same resource is reused.
                    hints[resolved] = (
                        max(v for v in (previous[0], width) if v is not None) if previous[0] or width else None,
                        max(v for v in (previous[1], height) if v is not None) if previous[1] or height else None,
                    )
    return hints


def _effective_target_box(
    profile: DeviceProfile,
    original_size: tuple[int, int],
    layout_hint: tuple[int | None, int | None] | None,
) -> tuple[int, int]:
    original_width, original_height = original_size
    screen_width, screen_height = profile.width, profile.height
    if profile.allow_landscape and original_width > original_height and screen_height > screen_width:
        screen_width, screen_height = screen_height, screen_width

    zoom = profile.max_zoom_factor
    if zoom is None:
        return screen_width, screen_height

    max_width = max(1, int(round(screen_width * zoom)))
    max_height = max(1, int(round(screen_height * zoom)))
    if not layout_hint:
        return max_width, max_height

    hinted_width, hinted_height = layout_hint
    if hinted_width is None and hinted_height is not None and original_height > 0:
        hinted_width = max(1, int(round(hinted_height * original_width / original_height)))
    if hinted_height is None and hinted_width is not None and original_width > 0:
        hinted_height = max(1, int(round(hinted_width * original_height / original_width)))
    if hinted_width is not None:
        max_width = min(max_width, max(1, int(round(hinted_width * zoom))))
    if hinted_height is not None:
        max_height = min(max_height, max(1, int(round(hinted_height * zoom))))
    return max_width, max_height


def _has_content_drm(archive: zipfile.ZipFile) -> bool:
    names = archive.namelist()
    encryption_name = _find_case_insensitive(names, "META-INF/encryption.xml")
    rights_name = _find_case_insensitive(names, "META-INF/rights.xml")

    if rights_name:
        try:
            rights = archive.read(rights_name).lower()
            if b"adept" in rights or b"encryptedkey" in rights:
                return True
        except Exception:
            return True

    if not encryption_name:
        return False

    try:
        root = ET.fromstring(archive.read(encryption_name))
    except Exception:
        # Unknown encryption metadata: fail closed rather than corrupting a book.
        return True

    encrypted_nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "EncryptedData"]
    if not encrypted_nodes:
        return False

    for encrypted in encrypted_nodes:
        algorithm = ""
        for node in encrypted.iter():
            if node.tag.rsplit("}", 1)[-1] == "EncryptionMethod":
                algorithm = (node.attrib.get("Algorithm") or "").strip()
                break
        if algorithm not in _FONT_ONLY_ENCRYPTION:
            return True
    return False


def _quantize_fixed_grays(image: Image.Image, levels: tuple[int, ...]) -> Image.Image:
    palette_image = Image.new("P", (1, 1))
    palette: list[int] = []
    for level in levels:
        palette.extend((level, level, level))
    palette.extend([0] * (768 - len(palette)))
    palette_image.putpalette(palette)
    rgb = image.convert("RGB")
    quantized = rgb.quantize(
        colors=len(levels),
        palette=palette_image,
        dither=Image.Dither.FLOYDSTEINBERG,
    )
    return quantized.convert("L")


def _flatten_alpha(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"}:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    if image.mode == "P" and "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image


def _encode_like_source(image: Image.Image, suffix: str, profile: DeviceProfile) -> bytes:
    out = io.BytesIO()
    suffix = suffix.lower()

    if suffix in {".jpg", ".jpeg"}:
        # Xteink/CrossPoint compatibility is safest with baseline RGB JPEG.
        encoded = image.convert("RGB") if profile.gray_levels is not None else image.convert("L")
        encoded.save(
            out,
            format="JPEG",
            quality=profile.jpeg_quality,
            optimize=True,
            progressive=False,
        )
    elif suffix == ".png":
        image.save(out, format="PNG", optimize=True, compress_level=9)
    elif suffix == ".webp":
        image.save(out, format="WEBP", quality=profile.jpeg_quality, method=6)
    elif suffix in {".tif", ".tiff"}:
        image.save(out, format="TIFF", compression="tiff_lzw")
    elif suffix == ".bmp":
        image.save(out, format="BMP")
    else:
        raise ValueError(f"unsupported image extension: {suffix}")
    return out.getvalue()


def _optimize_image(
    data: bytes,
    name: str,
    profile: DeviceProfile,
    *,
    layout_hint: tuple[int | None, int | None] | None = None,
    target_box: tuple[int, int] | None = None,
    max_image_pixels: int = 64_000_000,
) -> tuple[bytes, ImageChange]:
    suffix = Path(name).suffix.lower()
    try:
        with Image.open(io.BytesIO(data)) as opened:
            original_mode = opened.mode
            original_width, original_height = opened.size
            pixels = original_width * original_height
            if pixels > max_image_pixels:
                return data, ImageChange(
                    path=name, original_bytes=len(data), optimized_bytes=len(data),
                    original_width=original_width, original_height=original_height,
                    optimized_width=original_width, optimized_height=original_height,
                    original_mode=original_mode, optimized_mode=original_mode,
                    action=f"conservée (limite sécurité {max_image_pixels} pixels)",
                )
            if getattr(opened, "is_animated", False):
                return data, ImageChange(
                    path=name, original_bytes=len(data), optimized_bytes=len(data),
                    original_width=original_width, original_height=original_height,
                    optimized_width=original_width, optimized_height=original_height,
                    original_mode=original_mode, optimized_mode=original_mode,
                    action="conservée (animation)",
                )
            if opened.mode in {"RGBA", "LA"} or (opened.mode == "P" and "transparency" in opened.info):
                return data, ImageChange(
                    path=name, original_bytes=len(data), optimized_bytes=len(data),
                    original_width=original_width, original_height=original_height,
                    optimized_width=original_width, optimized_height=original_height,
                    original_mode=original_mode, optimized_mode=original_mode,
                    action="conservée (transparence)",
                )
            image = ImageOps.exif_transpose(opened).copy()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f"image illisible: {exc}") from exc

    if target_box is None:
        target_width, target_height = _effective_target_box(
            profile, (original_width, original_height), layout_hint
        )
    else:
        target_width = max(1, int(target_box[0]))
        target_height = max(1, int(target_box[1]))
    resized = False
    if image.width > target_width or image.height > target_height:
        image.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
        resized = True

    if profile.grayscale:
        image = image.convert("L")
        if profile.autocontrast_cutoff is not None:
            image = ImageOps.autocontrast(image, cutoff=profile.autocontrast_cutoff)
        if profile.contrast_factor != 1.0:
            image = ImageEnhance.Contrast(image).enhance(profile.contrast_factor)
        if profile.gray_levels:
            image = _quantize_fixed_grays(image, profile.gray_levels)

    optimized = _encode_like_source(image, suffix, profile)
    action_bits: list[str] = []
    if resized:
        action_bits.append(f"resize {original_width}×{original_height}→{image.width}×{image.height}")
    if profile.grayscale and original_mode not in {"1", "L"}:
        action_bits.append("niveaux de gris")
    if profile.gray_levels:
        action_bits.append(f"quantification {len(profile.gray_levels)} niveaux")
    action_bits.append(f"recompression {suffix.lstrip('.').upper()}")

    # If processing made a fully compatible image larger without changing its
    # effective rendering constraints, keep the original bytes.
    mandatory_change = resized or bool(profile.gray_levels) or (profile.grayscale and original_mode not in {"1", "L"})
    if len(optimized) >= len(data) and not mandatory_change:
        optimized = data
        action = "conservée (optimisation plus volumineuse)"
        optimized_mode = original_mode
        optimized_width, optimized_height = original_width, original_height
    else:
        action = ", ".join(action_bits)
        optimized_mode = image.mode
        optimized_width, optimized_height = image.size

    return optimized, ImageChange(
        path=name,
        original_bytes=len(data),
        optimized_bytes=len(optimized),
        original_width=original_width,
        original_height=original_height,
        optimized_width=optimized_width,
        optimized_height=optimized_height,
        original_mode=original_mode,
        optimized_mode=optimized_mode,
        action=action,
    )


# Opt-in worker mode: strict caps shared across the image batches, not a
# per-image allowance. A process receives only a bounded image blob, the
# immutable profile and the effective image hints, never ZIP/SQLite/GTK/DRM.
_CPU_BATCH_BYTES = 16 * 1024 * 1024
_CPU_WAIT_SECONDS = 120


def _epub_payloads(
    archive: zipfile.ZipFile,
    infos: list[zipfile.ZipInfo],
    image_names: set[str],
    profile: DeviceProfile,
    layout_hints: dict[str, tuple[int | None, int | None]],
    *,
    max_image_mb: int,
    max_image_pixels: int,
    cpu_workers: int,
    cancel_event: threading.Event | None,
):
    """Yield contents in ZIP order; at most N image jobs / 16 MiB in flight.

    Sequential mode is the historical default. Workers are spawned, never
    forked from a GTK process, and are terminated by the Pool context
    manager on cancellation, timeout, crash or archive-write failure.
    """
    if cpu_workers == 1:
        for info in infos:
            _check_cancel(cancel_event)
            yield info, archive.read(info), None
        return

    context = mp.get_context("spawn")
    with context.Pool(processes=cpu_workers, maxtasksperchild=64) as workers:
        index = 0
        while index < len(infos):
            _check_cancel(cancel_event)
            info = infos[index]
            # Never marshal oversized image bytes or any non-image payload
            # into workers; large entries still use bounded sequential code.
            if (info.filename not in image_names
                    or info.file_size > min(
                        max_image_mb * 1024 * 1024, _CPU_BATCH_BYTES
                    )):
                yield info, archive.read(info), None
                index += 1
                continue
            pending = []
            byte_count = 0
            while len(pending) < cpu_workers and index < len(infos):
                candidate = infos[index]
                if (candidate.filename not in image_names
                        or candidate.file_size > max_image_mb * 1024 * 1024
                        or byte_count + candidate.file_size > _CPU_BATCH_BYTES):
                    break
                _check_cancel(cancel_event)
                data = archive.read(candidate)
                if len(data) > _CPU_BATCH_BYTES - byte_count:
                    # Malformed ZIP declared size cannot enlarge the batch.
                    raise EpubOptimizationError("cpu_image_batch_limit")
                byte_count += len(data)
                result = workers.apply_async(
                    _optimize_image,
                    args=(data, candidate.filename, profile),
                    kwds={
                        "layout_hint": layout_hints.get(candidate.filename),
                        "max_image_pixels": max_image_pixels,
                    },
                )
                pending.append((candidate, data, result))
                index += 1
            for item, data, result in pending:
                deadline = time.monotonic() + _CPU_WAIT_SECONDS
                while True:
                    _check_cancel(cancel_event)
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise EpubOptimizationError("cpu_image_worker_timeout")
                    try:
                        transformed = result.get(timeout=min(0.2, left))
                        break
                    except mp.TimeoutError:
                        continue
                    except Exception as exc:
                        # Per-image decode errors preserve the old behaviour
                        # (keep bytes, report error), without aborting a book.
                        # Pool failures/timeouts are NOT silently downgraded.
                        if isinstance(exc, (EOFError, BrokenPipeError,
                                            ConnectionError)):
                            raise EpubOptimizationError(
                                "cpu_image_worker_failed"
                            ) from None
                        transformed = exc
                        break
                yield item, data, transformed


def _clone_info(info: zipfile.ZipInfo, *, compress_type: int) -> zipfile.ZipInfo:
    clone = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
    clone.compress_type = compress_type
    clone.comment = info.comment
    clone.extra = info.extra
    clone.create_system = info.create_system
    clone.create_version = info.create_version
    clone.extract_version = info.extract_version
    clone.flag_bits = info.flag_bits
    clone.volume = info.volume
    clone.internal_attr = info.internal_attr
    clone.external_attr = info.external_attr
    return clone


def default_output_path(input_path: str | os.PathLike[str], profile: DeviceProfile) -> Path:
    source = Path(input_path)
    return source.with_name(f"{source.stem} [{profile.suffix}].epub")


def analyze_epub(
    input_path: str | os.PathLike[str],
    *,
    profile: DeviceProfile,
    max_uncompressed_mb: int = 2048,
    max_image_mb: int = 64,
    max_image_pixels: int = 64_000_000,
) -> EpubAnalysis:
    """Inspect an EPUB cheaply enough for planning and estimate the output size.

    The estimate is deliberately conservative: semantically sensitive or
    over-limit images are counted at their current compressed size.
    """
    source = Path(input_path).expanduser().resolve()
    if source.suffix.lower() != ".epub" or not source.is_file():
        raise EpubOptimizationError("le fichier source doit être un EPUB existant")
    try:
        archive = zipfile.ZipFile(source, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise EpubOptimizationError(f"EPUB invalide : {exc}") from exc

    with archive as zin:
        infos = zin.infolist()
        _validate_archive_structure(
            zin,
            infos,
            max_uncompressed_mb=max_uncompressed_mb,
        )
        if _has_content_drm(zin):
            raise DrmProtectedError("EPUB chiffré/DRM : optimisation refusée")

        image_infos = [
            info for info in infos
            if not info.is_dir() and Path(info.filename).suffix.lower() in _IMAGE_EXTS
        ]
        hints = _layout_hints(zin, infos)
        source_bytes = source.stat().st_size
        image_zip_bytes = sum(max(0, int(info.compress_size)) for info in image_infos)
        estimated_images = 0
        candidates = 0
        semantic = 0
        over_limit = 0

        for info in image_infos:
            current_bytes = max(0, int(info.compress_size))
            if info.file_size > max_image_mb * 1024 * 1024:
                estimated_images += current_bytes
                over_limit += 1
                continue
            try:
                data = zin.read(info)
                with Image.open(io.BytesIO(data)) as opened:
                    width, height = opened.size
                    pixels = int(width) * int(height)
                    animated = bool(getattr(opened, "is_animated", False))
                    transparent = opened.mode in {"RGBA", "LA"} or (
                        opened.mode == "P" and "transparency" in opened.info
                    )
                    mode = opened.mode
            except Exception:
                estimated_images += current_bytes
                continue

            if pixels > max_image_pixels:
                estimated_images += current_bytes
                over_limit += 1
                continue
            if animated or transparent:
                estimated_images += current_bytes
                semantic += 1
                continue

            candidates += 1
            target_width, target_height = _effective_target_box(
                profile, (int(width), int(height)), hints.get(info.filename)
            )
            scale = min(
                1.0,
                target_width / max(1, int(width)),
                target_height / max(1, int(height)),
            )
            factor = max(0.18, min(1.0, scale * scale))
            if profile.grayscale and mode not in {"1", "L"}:
                factor *= 0.82
            if profile.gray_levels:
                factor *= 0.88
            estimated_images += max(1, int(current_bytes * max(0.18, min(1.0, factor))))

        zip_overhead_and_non_images = max(0, source_bytes - image_zip_bytes)
        estimated = min(source_bytes, max(1, zip_overhead_and_non_images + estimated_images))
        return EpubAnalysis(
            source_bytes=source_bytes,
            estimated_size_bytes=estimated,
            images_seen=len(image_infos),
            images_candidates=candidates,
            images_semantic_preserved=semantic,
            images_over_limit=over_limit,
            layout_hints=sum(1 for info in image_infos if info.filename in hints),
        )


def optimize_epub(
    input_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    profile: DeviceProfile,
    max_uncompressed_mb: int = 2048,
    max_image_mb: int = 64,
    max_image_pixels: int = 64_000_000,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    cpu_workers: int = 1,
) -> OptimizationReport:
    if type(cpu_workers) is not int or cpu_workers not in (1, 2, 4):
        raise EpubOptimizationError("cpu_workers doit valoir 1, 2 ou 4")
    source = Path(input_path).expanduser().resolve()
    target = Path(output_path).expanduser().resolve()

    if source == target:
        raise EpubOptimizationError("la sortie doit être différente de l'EPUB original")
    if source.suffix.lower() != ".epub":
        raise EpubOptimizationError("le fichier source doit être un EPUB")
    if not source.is_file():
        raise EpubOptimizationError("fichier source introuvable")
    target.parent.mkdir(parents=True, exist_ok=True)

    report = OptimizationReport(
        profile_id=profile.id,
        source_path=str(source),
        output_path=str(target),
        source_bytes=source.stat().st_size,
    )
    report.source_sha256 = _sha256(source)

    _emit(progress, 1, "Validation de l'EPUB…")
    _check_cancel(cancel_event)

    try:
        archive = zipfile.ZipFile(source, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise EpubOptimizationError(f"EPUB invalide : {exc}") from exc

    temp_name: str | None = None
    try:
        with archive as zin:
            infos = zin.infolist()
            mimetype_name = _validate_archive_structure(
                zin,
                infos,
                max_uncompressed_mb=max_uncompressed_mb,
            )

            if _has_content_drm(zin):
                raise DrmProtectedError("EPUB chiffré/DRM : optimisation refusée")

            image_infos = [
                info for info in infos
                if not info.is_dir() and Path(info.filename).suffix.lower() in _IMAGE_EXTS
            ]
            report.images_seen = len(image_infos)
            image_names = {info.filename for info in image_infos}
            layout_hints = _layout_hints(zin, infos)
            total_image_weight = max(1, sum(max(1, info.file_size) for info in image_infos))
            processed_weight = 0

            _emit(progress, 5, f"{len(image_infos)} image(s) détectée(s)")
            _check_cancel(cancel_event)

            fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
            os.close(fd)
            temp_path = Path(temp_name)

            with zipfile.ZipFile(temp_path, "w") as zout:
                zout.comment = zin.comment

                # EPUB requirement: mimetype first and uncompressed.
                mime_info = next(info for info in infos if info.filename == mimetype_name)
                mime_clone = _clone_info(mime_info, compress_type=zipfile.ZIP_STORED)
                zout.writestr(mime_clone, b"application/epub+zip")

                processed_index = 0
                contents = _epub_payloads(
                    zin, [info for info in infos if info.filename != mimetype_name],
                    image_names, profile, layout_hints,
                    max_image_mb=max_image_mb, max_image_pixels=max_image_pixels,
                    cpu_workers=cpu_workers, cancel_event=cancel_event,
                )
                for info, data, precomputed in contents:
                    _check_cancel(cancel_event)
                    if info.filename in image_names:
                        processed_index += 1
                        try:
                            if info.file_size > max_image_mb * 1024 * 1024:
                                report.images_kept += 1
                                report.image_notes.append(
                                    f"{info.filename}: conservée (image > {max_image_mb} Mio)"
                                )
                            else:
                                if isinstance(precomputed, Exception):
                                    raise precomputed
                                if precomputed is not None:
                                    optimized, change = precomputed
                                else:
                                    optimized, change = _optimize_image(
                                        data,
                                        info.filename,
                                        profile,
                                        layout_hint=layout_hints.get(info.filename),
                                        max_image_pixels=max_image_pixels,
                                    )
                                report.changes.append(change)
                                if optimized != data:
                                    report.images_optimized += 1
                                    data = optimized
                                else:
                                    report.images_kept += 1
                                    if "conservée (" in change.action:
                                        report.image_notes.append(f"{info.filename}: {change.action}")
                        except Exception as exc:
                            report.images_kept += 1
                            report.image_errors.append(f"{info.filename}: {exc}")
                        finally:
                            processed_weight += max(1, info.file_size)
                            pct = 5 + int(88 * processed_weight / total_image_weight)
                            _emit(
                                progress,
                                pct,
                                f"Image {processed_index}/{len(image_infos)} : {Path(info.filename).name}",
                            )

                    compression = zipfile.ZIP_DEFLATED
                    clone = _clone_info(info, compress_type=compression)
                    zout.writestr(clone, data, compress_type=compression, compresslevel=9)

            _check_cancel(cancel_event)
            _emit(progress, 95, "Validation de la sortie…")
            with zipfile.ZipFile(temp_path, "r") as check:
                first = check.infolist()[0]
                if first.filename != "mimetype" or first.compress_type != zipfile.ZIP_STORED:
                    raise EpubOptimizationError("EPUB produit invalide : mimetype")
                if check.read("mimetype") != b"application/epub+zip":
                    raise EpubOptimizationError("EPUB produit invalide : mimetype content")

            os.replace(temp_path, target)
            temp_name = None

        report.output_bytes = target.stat().st_size
        report.output_sha256 = _sha256(target)
        _emit(progress, 100, "EPUB optimisé")
        return report
    except CancelledError:
        raise
    except DrmProtectedError:
        raise
    except EpubOptimizationError:
        raise
    except Exception as exc:
        raise EpubOptimizationError(str(exc)) from exc
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
