from __future__ import annotations

import hashlib
import io
import os
import tempfile
import threading
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
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts


def _find_case_insensitive(names: list[str], wanted: str) -> str | None:
    lower = wanted.lower()
    for name in names:
        if name.lower() == lower:
            return name
    return None


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


def _optimize_image(data: bytes, name: str, profile: DeviceProfile) -> tuple[bytes, ImageChange]:
    suffix = Path(name).suffix.lower()
    try:
        with Image.open(io.BytesIO(data)) as opened:
            if getattr(opened, "is_animated", False):
                opened.seek(0)
            image = ImageOps.exif_transpose(opened).copy()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"image illisible: {exc}") from exc

    original_mode = image.mode
    original_width, original_height = image.size
    image = _flatten_alpha(image)

    resized = False
    if image.width > profile.width or image.height > profile.height:
        image.thumbnail((profile.width, profile.height), Image.Resampling.LANCZOS)
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


def optimize_epub(
    input_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    profile: DeviceProfile,
    max_uncompressed_mb: int = 2048,
    progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> OptimizationReport:
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
            if not infos:
                raise EpubOptimizationError("EPUB vide")
            for info in infos:
                if not _safe_entry_name(info.filename):
                    raise EpubOptimizationError(f"chemin ZIP dangereux : {info.filename}")

            names = [info.filename for info in infos]
            mimetype_name = _find_case_insensitive(names, "mimetype")
            if mimetype_name is None:
                raise EpubOptimizationError("EPUB sans entrée mimetype")
            if zin.read(mimetype_name).strip() != b"application/epub+zip":
                raise EpubOptimizationError("mimetype EPUB invalide")

            total_uncompressed = sum(info.file_size for info in infos)
            if total_uncompressed > max_uncompressed_mb * 1024 * 1024:
                raise EpubOptimizationError(
                    f"EPUB décompressé trop volumineux ({total_uncompressed / 1024 / 1024:.0f} Mio > {max_uncompressed_mb} Mio)"
                )

            if _has_content_drm(zin):
                raise DrmProtectedError("EPUB chiffré/DRM : optimisation refusée")

            image_infos = [
                info for info in infos
                if not info.is_dir() and Path(info.filename).suffix.lower() in _IMAGE_EXTS
            ]
            report.images_seen = len(image_infos)
            image_names = {info.filename for info in image_infos}

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
                for info in infos:
                    _check_cancel(cancel_event)
                    if info.filename == mimetype_name:
                        continue

                    data = zin.read(info)
                    if info.filename in image_names:
                        processed_index += 1
                        pct = 5 + int(88 * processed_index / max(1, len(image_infos)))
                        _emit(progress, pct, f"Image {processed_index}/{len(image_infos)} : {Path(info.filename).name}")
                        try:
                            optimized, change = _optimize_image(data, info.filename, profile)
                            report.changes.append(change)
                            if optimized != data:
                                report.images_optimized += 1
                                data = optimized
                            else:
                                report.images_kept += 1
                        except Exception as exc:
                            report.images_kept += 1
                            report.image_errors.append(f"{info.filename}: {exc}")

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
