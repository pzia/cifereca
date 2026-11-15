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
import sys

# Also importable by offline tests via importlib.util (where e2e/ is not on
# sys.path), without reading any local user data or importing GTK.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paired_book_review_fixture import verified_fixture_receipt

ROOT = Path(__file__).resolve().parents[1]
SIZES = {1024: (1024, 768), 1366: (1366, 900), 1600: (1600, 1000)}
SURFACES = {
    "search": ("search-filter", "00-before", "screenshot", "GTK4/X11/Xvfb",
               "P1_774_search_real_matched_geometry", "p1_774_matched_geometry"),
    "recommendations": (
        "recommendations-detail", "09-paired-public-book", "image", "GTK4/X11/XTEST",
        "P1_774_real_gtk_logical_hidpi_geometry", "p1_774_geometry",
    ),
}
# The primary pair shows ONE exact synthetic book. Supplementary paired
# sheets show independent workflow states, NOT the same selected book.
# Every stage is already captured by the real GTK/XTEST probes.
WORKFLOW_STAGES = {
    "filters": {
        "search": ("07-response-facets", "response_facets_descriptive_and_scoped"),
        "recommendations": (
            "00b-keyboard-local-filter", "P1_774_real_gtk_xtest_keyboard_local_filter",
        ),
    },
    "states": {
        "search": (
            "06-stale-local-reply-ignored", "stale_local_reply_ignored_after_query_edit",
        ),
        "recommendations": (
            "08-exact-in-place-unverified-holdings",
            "P1_4664_gtk_failure_preserves_core_actions",
        ),
    },
}
CAPTURES_PER_WIDTH = 2 * (1 + len(WORKFLOW_STAGES))
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
    *, workflow: str | None = None,
) -> tuple[str, Path, str, str, str]:
    from PIL import Image

    prefix, stage, field, backend, required_check, geometry_key = SURFACES[surface]
    if workflow is not None:
        if workflow not in WORKFLOW_STAGES:
            raise ReviewError("Étape de comparaison UX inconnue")
        stage, workflow_check = WORKFLOW_STAGES[workflow][surface]
    folder = root / f"{prefix}-{width}"
    if folder.is_symlink() or not folder.is_dir():
        raise ReviewError("Répertoire de capture absent ou lié")
    report_path = folder / "screen.json"
    _regular(report_path, limit=4 * 1024 * 1024)
    try:
        # Parse and fingerprint the EXACT bytes that the GTK probe wrote.
        # The complete report (not only selected PASS flags) is evidence.
        report_bytes = report_path.read_bytes()
        report = json.loads(report_bytes.decode("utf-8"))
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
    # Only the principal pair certifies the SAME synthetic book. The
    # additional workflow screens show distinct books/contexts and must
    # never be conflated with the exact-edition receipt.
    if workflow is not None:
        if report["checks"].get(workflow_check) is not True:
            raise ReviewError("État de parcours GTK non attesté")
    else:
        paired_check = (
            "P1_774_search_selected_same_synthetic_book"
            if surface == "search"
            else "P1_774_recommendations_selected_same_synthetic_book"
        )
        heading_check = (
            "P1_774_search_mapped_same_book_detail"
            if surface == "search"
            else "P1_774_recommendations_mapped_same_book_detail"
        )
        subject_check = (
            "P1_4761_search_public_subject_in_real_gtk_detail"
            if surface == "search"
            else "P1_4761_recommendations_public_subject_in_real_gtk_detail"
        )
        # Search exposes a real author facet; Recommendations must also
        # keep a keyboard-usable public author query affordance in its
        # Catalogue fields. Only the Recommendations probe emits this check.
        catalogue_actions_ok = (
            surface != "recommendations"
            or report["checks"].get(
                "P1_4761_recommendations_typed_catalogue_author_gtk"
            ) is True
        )
        # The actual GTK SingleSelection must preserve the exact edition
        # under local source/field filters, including colliding raw keys.
        # No completed private UX grid may bypass this paired-book path.
        filter_selection_ok = (
            surface != "recommendations"
            or report["checks"].get(
                "P1_4761_filter_preserves_exact_edition_real_gtk"
            ) is True
        )
        # #4761: BOTH real GTK surfaces must attest the same public
        # compact Source/Format/Language inside the Core detail heading.
        # The original Search screenshot alone does not establish that
        # its metadata was painted, even when its title/author were mapped.
        compact_header_check = (
            "P1_4761_search_shared_compact_essential_gtk"
            if surface == "search" else
            "P1_4761_recommendations_shared_compact_essential_gtk"
        )
        compact_header_ok = report["checks"].get(compact_header_check) is True
        # An obsolete typed author action must also have been exercised
        # in GTK before accepting a paired visual-review checklist.
        stale_action_inert_ok = (
            surface != "recommendations"
            or report["checks"].get(
                "P1_4761_stale_metadata_search_button_inert_gtk"
            ) is True
        )
        if (report["checks"].get(paired_check) is not True
                or report["checks"].get(heading_check) is not True
                or report["checks"].get(subject_check) is not True
                or not catalogue_actions_ok
                or not compact_header_ok
                or not stale_action_inert_ok
                or not filter_selection_ok
                or report.get("p1_774_paired_book") != verified_fixture_receipt()):
            raise ReviewError(
                "Source/édition, détail GTK ou sujet commun non attesté"
            )
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
    return sha, image_path, actual, appearance, hashlib.sha256(report_bytes).hexdigest()


def verified_pair(
    root: Path, *, sizes: dict[int, tuple[int, int]] = SIZES,
    expected_sha: str | None = None,
    expected_appearance: str | None = None,
) -> tuple[str, str, str, dict[int, dict[str, Path]], str]:
    root = root.resolve(strict=True)
    all_images: dict[int, dict[str, Path]] = {}
    commits: set[str] = set()
    appearances: set[str] = set()
    fingerprint = hashlib.sha256()
    report_fingerprint = hashlib.sha256()
    fingerprint.update(
        ("book:" + verified_fixture_receipt()["document_sha256"] + "\n").encode("ascii")
    )
    for width, size in sorted(sizes.items()):
        images = {}
        report_digests = {}
        for surface in ("search", "recommendations"):
            sha, path, image_sha, appearance, report_sha = _report_and_image(
                root, width, size, surface,
            )
            commits.add(sha)
            appearances.add(appearance)
            images[surface] = path
            report_digests[surface] = report_sha
            # One full probe report per width/surface. The three images of
            # each surface come from the same report; do not hash it 3 times.
            report_fingerprint.update(
                f"{width}:{surface}:{report_sha}\n".encode("ascii")
            )
            fingerprint.update(
                f"{width}:{surface}:{appearance}:{image_sha}\n".encode("ascii")
            )
        # Supplemental pictures support filter and partial/error UX axes.
        # They come from the SAME GTK report/HEAD but may show DIFFERENT
        # books or profiles; no edition identity is claimed for these shots.
        for workflow in WORKFLOW_STAGES:
            for surface in ("search", "recommendations"):
                sha, path, image_sha, mode, report_sha = _report_and_image(
                    root, width, size, surface, workflow=workflow,
                )
                if report_sha != report_digests[surface]:
                    raise ReviewError("Rapport GTK changé pendant la vérification")
                commits.add(sha)
                appearances.add(mode)
                images[f"{workflow}:{surface}"] = path
                fingerprint.update(
                    f"{width}:{workflow}:{surface}:{mode}:{image_sha}\n".encode("ascii")
                )
        all_images[width] = images
    if len(commits) != 1 or (expected_sha is not None
                             and commits != {expected_sha}):
        raise ReviewError("Captures issues de HEAD différents ou périmés")
    if len(appearances) != 1 or (expected_appearance is not None
                                 and appearances != {expected_appearance}):
        raise ReviewError("Captures GTK clair/sombre incompatibles")
    return (
        next(iter(commits)), fingerprint.hexdigest(),
        next(iter(appearances)), all_images, report_fingerprint.hexdigest(),
    )


def _private_file(folder: Path, name: str) -> Path:
    path = folder / name
    if path.is_symlink() or (path.exists() and (
        not path.is_file() or path.stat().st_nlink != 1
    )):
        raise ReviewError("Destination de revue privée non sûre")
    return path


def _sheet(
    images: dict[str, Path], target: Path, width: int, appearance: str,
    *, workflow: str | None = None,
) -> None:
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
                  f"{width} px - {surface} - {appearance} - "
                  + (workflow + " (fixtures distinctes)" if workflow else "même notice"),
                  fill=(20, 30, 40))
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
    sha, fingerprint, appearance, images, probe_fingerprint = verified_pair(
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
                    or recorded.get("probe_evidence_sha256") != probe_fingerprint
                    or recorded.get("appearance") != appearance
                    or recorded.get("document_sha256") !=
                        verified_fixture_receipt()["document_sha256"]
                    or recorded.get("widths") != sorted(sizes)):
                raise ReviewError("Recette modifiée : préserver la grille privée antérieure")
        elif checklist.exists():
            raise ReviewError("Grille de revue privée sans manifeste")
        for width, paths in images.items():
            _sheet(paths, _private_file(output, f"pair-{width}.png"), width, appearance)
            for workflow in WORKFLOW_STAGES:
                _sheet(
                    {
                        surface: paths[f"{workflow}:{surface}"]
                        for surface in ("search", "recommendations")
                    },
                    _private_file(output, f"pair-{width}-{workflow}.png"),
                    width, appearance, workflow=workflow,
                )
        receipt: dict[str, object] = {
            "status": "VISUAL_REVIEW_REQUIRED",
            "git_sha": sha, "capture_fingerprint_sha256": fingerprint,
            "probe_evidence_sha256": probe_fingerprint,
            "appearance": appearance,
            "document_sha256": verified_fixture_receipt()["document_sha256"],
            "widths": sorted(sizes),
            "verified_source_captures": CAPTURES_PER_WIDTH * len(sizes),
            "workflow_sheets": sorted(WORKFLOW_STAGES),
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
    sha, fingerprint, appearance, _images, probe_fingerprint = verified_pair(
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
            or manifest.get("probe_evidence_sha256") != probe_fingerprint
            or manifest.get("appearance") != appearance
            or manifest.get("document_sha256") !=
                verified_fixture_receipt()["document_sha256"]
            or manifest.get("widths") != sorted(sizes)
            or manifest.get("verified_source_captures") != (
                CAPTURES_PER_WIDTH * len(sizes)
            )
            or manifest.get("workflow_sheets") != sorted(WORKFLOW_STAGES)):
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
        if verdict == "KO" and not (row["notes"] or "").strip():
            # Keep the diagnosis exclusively inside the local checklist.
            # An unexplained failure cannot be handed to the UX/CoreUI
            # reviewer, and must not become a valid numeric receipt.
            raise ReviewError("Anomalie UX KO sans diagnostic privé")
        failures += int(verdict == "KO")
    if seen != expected:
        raise ReviewError("Revue humaine incomplète")
    receipt: dict[str, object] = {
        "status": "CHECKLIST_VERIFIED", "git_sha": sha,
        "appearance": appearance,
        "document_sha256": verified_fixture_receipt()["document_sha256"],
        "capture_fingerprint_sha256": fingerprint,
        "probe_evidence_sha256": probe_fingerprint,
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
