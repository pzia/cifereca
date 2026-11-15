#!/usr/bin/env python3
"""P1 #774: PRIVATE, SHA-bound comparison of real Search and Recommendations GTK.

This is a review aid, not an automated UX verdict. No images, report bodies,
identities, titles, screenshots or notes are uploaded to GitHub Actions.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SIZES = {1024: (1024, 768), 1366: (1366, 900), 1600: (1600, 1000)}
SURFACES = {
    "search": ("search-filter", "00-before", "screenshot", "GTK4/X11/Xvfb",
               "P1_774_search_real_matched_geometry", "p1_774_matched_geometry"),
    "recommendations": (
        "recommendations-detail", "01-selected-a", "image", "GTK4/X11/XTEST",
        "P1_774_real_gtk_logical_hidpi_geometry", "p1_774_geometry",
    ),
}
REVIEW_AXES = (
    "layout_density", "filter_affordances", "keyboard_focus",
    "book_detail", "cover_consistency", "clipping", "partial_error_states",
)
REVIEW_FIELDS = ("width", *REVIEW_AXES, "ux_verdict", "notes")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")


class ReviewError(ValueError):
    """A closed failure; do not expose the potentially private report body."""


def _regular(path: Path, *, limit: int | None = None) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ReviewError("Fichier de recette absent ou non sûr")
    if limit is not None and path.stat().st_size > limit:
        raise ReviewError("Fichier de recette trop volumineux")


def _report_and_image(
    root: Path, width: int, size: tuple[int, int], surface: str,
) -> tuple[str, Path, str, str]:
    from PIL import Image

    prefix, stage, field, backend, required_check, geometry_key = SURFACES[surface]
    folder = root / f"{prefix}-{width}"
    if folder.is_symlink() or not folder.is_dir():
        raise ReviewError("Répertoire de capture absent ou lié")
    report_path = folder / "screen.json"
    _regular(report_path, limit=4 * 1024 * 1024)
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ReviewError("Rapport de capture illisible") from exc
    if not isinstance(report, dict) or report.get("status") != "PASS":
        raise ReviewError("Recette GTK non validée")
    expected_scenario = (
        f"search-filter-{width}" if surface == "search"
        else "recommendations-detail-gtk"
    )
    if (report.get("scenario") != expected_scenario
            or report.get("backend") != backend):
        raise ReviewError("Rapport non issu du scénario GTK attendu")
    if surface == "recommendations" and (
        type(report.get("width")) is not int or report["width"] != width
        or type(report.get("scale")) is not int or report["scale"] != 1
    ):
        raise ReviewError("Résolution logique Recommendations incorrecte")
    sha = report.get("git_sha")
    if not isinstance(sha, str) or SHA_PATTERN.fullmatch(sha) is None:
        raise ReviewError("SHA Git de recette absent ou invalide")
    # The probes record the *observed* Adw.StyleManager state at capture
    # time; Search and Recommendations must NOT be compared across themes.
    appearance = report.get("appearance")
    if type(appearance) is not str or appearance not in ("light", "dark"):
        raise ReviewError("Apparence GTK de capture non attestée")
    if not isinstance(report.get("checks"), dict) or (
        report["checks"].get(required_check) is not True
    ):
        raise ReviewError("Preuve de géométrie GTK absente")
    geometry = report.get(geometry_key)
    if (not isinstance(geometry, dict)
            or set(geometry) != {
                "display_width", "display_height", "window_width", "window_height",
            }
            or any(type(v) is not int for v in geometry.values())
            or (geometry["display_width"], geometry["display_height"]) != size):
        raise ReviewError("Géométrie physique non attestée")
    inset = 150 if surface == "search" else 100
    if not (width - inset <= geometry["window_width"] <= width
            and 500 <= geometry["window_height"] <= size[1]):
        raise ReviewError("Fenêtre GTK hors budget")
    steps = report.get("steps")
    if not isinstance(steps, list):
        raise ReviewError("Étapes GTK manquantes")
    captures = [step for step in steps
                if isinstance(step, dict) and step.get("stage") == stage]
    if len(captures) != 1:
        raise ReviewError("Capture de référence absente ou dupliquée")
    entry = captures[0]
    if entry.get("appearance") != appearance:
        raise ReviewError("Apparence GTK incohérente entre étapes et rapport")
    name = entry.get(field)
    digest = entry.get("sha256")
    if name != f"{stage}.png" or not isinstance(digest, str) or (
        DIGEST_PATTERN.fullmatch(digest) is None
    ):
        raise ReviewError("Nom ou empreinte de capture incohérent")
    image_path = folder / name
    _regular(image_path, limit=40 * 1024 * 1024)
    actual = hashlib.sha256(image_path.read_bytes()).hexdigest()
    if actual != digest:
        raise ReviewError("Image GTK modifiée depuis sa capture")
    try:
        with Image.open(image_path) as image:
            if image.format != "PNG" or image.size != size:
                raise ReviewError("Image GTK ou taille physique invalide")
            image.verify()
    except (OSError, ValueError) as exc:
        raise ReviewError("Image de capture illisible") from exc
    return sha, image_path, actual, appearance


def verified_pair(
    root: Path, *, sizes: dict[int, tuple[int, int]] = SIZES,
    expected_sha: str | None = None,
    expected_appearance: str | None = None,
) -> tuple[str, str, str, dict[int, dict[str, Path]]]:
    root = root.resolve(strict=True)
    all_images: dict[int, dict[str, Path]] = {}
    commits: set[str] = set()
    appearances: set[str] = set()
    fingerprint = hashlib.sha256()
    for width, size in sorted(sizes.items()):
        images = {}
        for surface in ("search", "recommendations"):
            sha, path, image_sha, appearance = _report_and_image(root, width, size, surface)
            commits.add(sha)
            appearances.add(appearance)
            images[surface] = path
            fingerprint.update(
                f"{width}:{surface}:{appearance}:{image_sha}\n".encode("ascii")
            )
        all_images[width] = images
    if len(commits) != 1 or (expected_sha is not None
                             and commits != {expected_sha}):
        raise ReviewError("Captures issues de HEAD différents ou périmés")
    if len(appearances) != 1 or (expected_appearance is not None
                                 and appearances != {expected_appearance}):
        raise ReviewError("Captures GTK clair/sombre incompatibles")
    return next(iter(commits)), fingerprint.hexdigest(), next(iter(appearances)), all_images


def _private_file(folder: Path, name: str) -> Path:
    path = folder / name
    if path.is_symlink() or (path.exists() and (
        not path.is_file() or path.stat().st_nlink != 1
    )):
        raise ReviewError("Destination de revue privée non sûre")
    return path


def _sheet(images: dict[str, Path], target: Path, width: int, appearance: str) -> None:
    from PIL import Image, ImageDraw

    tile_width, margin, header = 660, 16, 36
    with Image.open(images["search"]) as src:
        tile_height = round(src.height * tile_width / src.width)
    board = Image.new(
        "RGB", (tile_width * 2 + margin * 3,
                tile_height + header + margin * 2), (242, 244, 247),
    )
    draw = ImageDraw.Draw(board)
    for index, surface in enumerate(("search", "recommendations")):
        left = margin + index * (tile_width + margin)
        draw.text((left + 4, margin + 8),
                  f"{width} px - {surface} - GTK X11 - {appearance}", fill=(20, 30, 40))
        with Image.open(images[surface]) as picture:
            tile = picture.convert("RGB").resize(
                (tile_width, tile_height), Image.Resampling.LANCZOS,
            )
            board.paste(tile, (left, margin + header))
    board.save(target)
    target.chmod(0o600)


def build_review(
    root: Path, *, sizes: dict[int, tuple[int, int]] = SIZES,
    expected_sha: str | None = None,
    expected_appearance: str | None = None,
) -> dict[str, object]:
    sha, fingerprint, appearance, images = verified_pair(
        root, sizes=sizes, expected_sha=expected_sha,
        expected_appearance=expected_appearance,
    )
    output = root.resolve(strict=True) / "search-recommendations-private-review"
    if output.is_symlink():
        raise ReviewError("Répertoire de revue privée lié")
    old_umask = os.umask(0o077)
    try:
        output.mkdir(mode=0o700, exist_ok=True)
        output.chmod(0o700)
        manifest = _private_file(output, "manifest.json")
        checklist = _private_file(output, "ux-checklist.csv")
        if manifest.exists():
            _regular(manifest, limit=256 * 1024)
            recorded = json.loads(manifest.read_text(encoding="utf-8"))
            if (recorded.get("git_sha") != sha
                    or recorded.get("capture_fingerprint_sha256") != fingerprint
                    or recorded.get("appearance") != appearance
                    or recorded.get("widths") != sorted(sizes)):
                raise ReviewError("Recette modifiée : préserver la grille privée antérieure")
        elif checklist.exists():
            raise ReviewError("Grille de revue privée sans manifeste")
        for width, paths in images.items():
            _sheet(paths, _private_file(output, f"pair-{width}.png"), width, appearance)
        receipt: dict[str, object] = {
            "status": "VISUAL_REVIEW_REQUIRED",
            "git_sha": sha, "capture_fingerprint_sha256": fingerprint,
            "appearance": appearance,
            "widths": sorted(sizes), "verified_source_captures": 2 * len(sizes),
        }
        manifest.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        manifest.chmod(0o600)
        if not checklist.exists():
            with checklist.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(REVIEW_FIELDS)
                for width in sorted(sizes):
                    writer.writerow([width] + [""] * (len(REVIEW_FIELDS) - 1))
            checklist.chmod(0o600)
        return receipt
    finally:
        os.umask(old_umask)


def verify_completed_review(
    root: Path, *, sizes: dict[int, tuple[int, int]] = SIZES,
    expected_sha: str | None = None,
    expected_appearance: str | None = None,
) -> dict[str, object]:
    sha, fingerprint, appearance, _images = verified_pair(
        root, sizes=sizes, expected_sha=expected_sha,
        expected_appearance=expected_appearance,
    )
    output = root.resolve(strict=True) / "search-recommendations-private-review"
    if output.is_symlink() or not output.is_dir():
        raise ReviewError("Revue privée absente")
    manifest_path = _private_file(output, "manifest.json")
    checklist = _private_file(output, "ux-checklist.csv")
    _regular(manifest_path, limit=256 * 1024)
    _regular(checklist, limit=256 * 1024)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("status") != "VISUAL_REVIEW_REQUIRED"
            or manifest.get("git_sha") != sha
            or manifest.get("capture_fingerprint_sha256") != fingerprint
            or manifest.get("appearance") != appearance
            or manifest.get("widths") != sorted(sizes)
            or manifest.get("verified_source_captures") != 2 * len(sizes)):
        raise ReviewError("Grille et captures ne correspondent plus")
    raw = checklist.read_bytes()
    with checklist.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != REVIEW_FIELDS:
            raise ReviewError("Colonnes de revue incorrectes")
        rows = list(reader)
    expected = {str(width) for width in sizes}
    seen = set()
    failures = exclusions = 0
    for row in rows:
        width = row["width"]
        if width not in expected or width in seen:
            raise ReviewError("Ligne privée manquante, répétée ou inconnue")
        seen.add(width)
        verdict = row["ux_verdict"]
        axes = [row[key] for key in REVIEW_AXES]
        if (any(axis not in {"OK", "KO", "N/A"} for axis in axes)
                or verdict not in {"OK", "KO"}):
            raise ReviewError("Revue humaine incomplète")
        if "N/A" in axes:
            exclusions += 1
            if not (row["notes"] or "").strip():
                raise ReviewError("Exclusion sans justification privée")
        if "KO" in axes and verdict != "KO":
            raise ReviewError("Anomalie UX masquée par un verdict OK")
        failures += int(verdict == "KO")
    if seen != expected:
        raise ReviewError("Revue humaine incomplète")
    receipt: dict[str, object] = {
        "status": "CHECKLIST_VERIFIED", "git_sha": sha,
        "appearance": appearance,
        "capture_fingerprint_sha256": fingerprint,
        "checklist_sha256": hashlib.sha256(raw).hexdigest(),
        "reviewed_widths": len(seen), "findings": failures,
        "widths_with_exclusions": exclusions,
        "ready_for_ux_decision": failures == 0 and exclusions == 0,
    }
    old_umask = os.umask(0o077)
    try:
        dest = _private_file(output, "visual-review-verdict.json")
        dest.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        dest.chmod(0o600)
    finally:
        os.umask(old_umask)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("e2e-artifacts"))
    parser.add_argument("--verify-checklist", action="store_true")
    parser.add_argument("--appearance", choices=("light", "dark"), required=True,
                        help="Apparence GTK réellement attendue : jamais inférée de la demande")
    args = parser.parse_args()
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            timeout=5, stderr=subprocess.DEVNULL,
        ).strip()
        if SHA_PATTERN.fullmatch(sha) is None:
            raise ReviewError("HEAD Git invalide")
        if args.verify_checklist:
            result = verify_completed_review(
                args.root, expected_sha=sha, expected_appearance=args.appearance,
            )
        else:
            result = build_review(
                args.root, expected_sha=sha, expected_appearance=args.appearance,
            )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # Errors are closed strings, not raw report bodies or GTK exceptions.
        detail = str(exc) if isinstance(exc, ReviewError) else "entrée privée invalide"
        print("REFUS RECETTE P1 #774 : " + detail)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
