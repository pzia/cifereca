#!/usr/bin/env python3
"""P1 #757: generate PRIVATE light/dark contact sheets from real GTK captures.

This tool never uploads images or certifies UX. Run it on the authorized GTK
host after both modern-light scenarios passed, then inspect original PNGs.
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
from typing import Any

STAGES = (
    "dashboard", "search", "search-detail", "library", "loans",
    "sync", "plugins", "settings", "constellations",
)
SIZES = {1366: (1366, 900), 1600: (1600, 1000)}
APPEARANCES = ("light", "dark")


def _private_destination(folder: Path, name: str) -> Path:
    """Refuse to overwrite links or non-regular files in the private review."""
    target = folder / name
    if target.is_symlink() or (
        target.exists() and (
            not target.is_file() or target.stat().st_nlink != 1
        )
    ):
        raise ValueError("Destination de revue privée non sûre")
    return target


def verify_capture_folder(
    folder: Path, *, width: int, size: tuple[int, int],
    stages: tuple[str, ...] = STAGES,
) -> tuple[str, dict[tuple[str, str], Path]]:
    """Reject missing, swapped, edited, non-GTK or unsafe screenshots."""
    from PIL import Image

    folder = folder.resolve(strict=True)
    report = json.loads((folder / "screen.json").read_text(encoding="utf-8"))
    if report.get("status") != "PASS" or report.get("width") != width:
        raise ValueError(f"{folder.name}: verdict/largeur de capture incorrect")
    if report.get("backend") != "GTK4/X11":
        raise ValueError(f"{folder.name}: source GTK réelle non attestée")
    sha = report.get("git_sha")
    if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise ValueError(f"{folder.name}: SHA de commit absent ou invalide")
    images: dict[tuple[str, str], Path] = {}
    captures = report.get("screenshots")
    if not isinstance(captures, list):
        raise ValueError(f"{folder.name}: liste de captures absente")
    expected = {(stage, mode) for stage in stages for mode in APPEARANCES}
    for item in captures:
        if not isinstance(item, dict):
            raise ValueError("Capture non structurée")
        key = item.get("stage"), item.get("appearance")
        if key not in expected or key in images:
            raise ValueError(f"Capture manquante, répétée ou inattendue : {key}")
        name = item.get("file")
        expected_name = (
            r"[0-9]{2}-" + re.escape(key[0]) + "-"
            + re.escape(key[1]) + r"\.png"
        )
        if not isinstance(name, str) or re.fullmatch(expected_name, name) is None:
            raise ValueError("Nom de capture incohérent avec la vue ou le thème")
        candidate = folder / name
        if candidate.is_symlink():
            raise ValueError("Capture liée symboliquement")
        path = candidate.resolve(strict=True)
        if path.parent != folder:
            raise ValueError("Capture extérieure au répertoire de recette")
        digest = item.get("sha256")
        if not isinstance(digest, str) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"{folder.name}: empreinte de capture incorrecte")
        with Image.open(path) as picture:
            if picture.format != "PNG" or picture.size != size:
                raise ValueError(f"{folder.name}: image ou géométrie incorrecte")
            picture.verify()
        images[key] = path
    if set(images) != expected:
        missing = sorted(expected - set(images))
        raise ValueError(f"{folder.name}: captures absentes : {missing}")
    return sha, images


def contact_sheets(
    images: dict[tuple[str, str], Path], output: Path, *,
    width: int, stages: tuple[str, ...] = STAGES, tile_width: int = 850,
) -> list[str]:
    from PIL import Image, ImageDraw

    names = []
    margin, header, per_page = 16, 28, 3
    for first in range(0, len(stages), per_page):
        chosen = stages[first:first + per_page]
        with Image.open(images[(chosen[0], "light")]) as original:
            scaled_height = round(original.height * tile_width / original.width)
        canvas = Image.new(
            "RGB", (tile_width * 2 + margin * 3,
                    (scaled_height + header + margin) * len(chosen) + margin),
            (240, 243, 248),
        )
        pen = ImageDraw.Draw(canvas)
        for row, stage in enumerate(chosen):
            top = margin + row * (scaled_height + header + margin)
            for col, mode in enumerate(APPEARANCES):
                left = margin + col * (tile_width + margin)
                pen.text((left + 4, top + 5), f"{width} · {stage} · {mode}",
                         fill=(10, 20, 40))
                with Image.open(images[(stage, mode)]) as original:
                    tile = original.convert("RGB").resize(
                        (tile_width, scaled_height), Image.Resampling.LANCZOS,
                    )
                    canvas.paste(tile, (left, top + header))
        name = f"modern-light-{width}-{first // per_page + 1:02d}.png"
        target = _private_destination(output, name)
        canvas.save(target)
        target.chmod(0o600)
        names.append(name)
    return names


def _capture_fingerprint(
    verified: dict[int, dict[tuple[str, str], Path]],
) -> str:
    """Bind a private review to the *actual PNG bytes*, not just Git HEAD.

    Re-running the GTK scenario on the same commit may produce different
    pixels. A completed UX checklist must not silently certify that rerun.
    """
    digest = hashlib.sha256()
    for width, images in sorted(verified.items()):
        for (stage, mode), path in sorted(images.items()):
            digest.update(f"{width}\\0{stage}\\0{mode}\\0".encode("utf-8"))
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def build_review(
    root: Path, *, sizes: dict[int, tuple[int, int]] = SIZES,
    stages: tuple[str, ...] = STAGES, tile_width: int = 850,
    expected_sha: str | None = None,
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    verified = {}
    revisions = set()
    for width, size in sizes.items():
        revision, images = verify_capture_folder(
            root / f"modern-light-{width}", width=width, size=size, stages=stages,
        )
        revisions.add(revision)
        verified[width] = images
    if len(revisions) != 1:
        raise ValueError("Les deux résolutions proviennent de HEAD différents")
    if expected_sha is not None and revisions != {expected_sha}:
        raise ValueError("Captures réalisées sur un HEAD différent du checkout")
    capture_fingerprint = _capture_fingerprint(verified)
    # Never place private screenshots in a public CI artifact destination.
    output = root / "modern-light-private-review"
    if output.is_symlink():
        raise ValueError("Répertoire de revue lié symboliquement")
    old_umask = os.umask(0o077)
    try:
        output.mkdir(mode=0o700, exist_ok=True)
        output.chmod(0o700)
        # A human may have filled the checklist already. Preserve those
        # judgments on a repeat for the same HEAD, never mix with a new HEAD.
        prior_manifest = _private_destination(output, "manifest.json")
        checklist = _private_destination(output, "ux-checklist.csv")
        previous = prior_manifest.exists()
        if previous:
            stored = json.loads(prior_manifest.read_text(encoding="utf-8"))
            if stored.get("git_sha") != next(iter(revisions)):
                raise ValueError("Revue privée existante issue d'un autre HEAD")
            if stored.get("capture_fingerprint_sha256") != capture_fingerprint:
                raise ValueError("Captures différentes sur le même HEAD : nouvelle revue UX requise")
        elif checklist.exists():
            raise ValueError("Grille UX préexistante sans manifeste vérifiable")
        sheets = {}
        for width, images in verified.items():
            sheets[str(width)] = contact_sheets(
                images, output, width=width, stages=stages, tile_width=tile_width,
            )
        manifest: dict[str, Any] = {
            "status": "VISUAL_REVIEW_REQUIRED",
            "git_sha": next(iter(revisions)),
            "capture_fingerprint_sha256": capture_fingerprint,
            "widths": sorted(sizes),
            "verified_original_captures": sum(len(x) for x in verified.values()),
            "contact_sheets": sheets,
            "note": "Planche de navigation, non-certification UX ; ouvrir les PNG originaux pour les détails.",
        }
        prior_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        prior_manifest.chmod(0o600)
        if not checklist.exists():
            with checklist.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow([
                    "width", "stage", "appearance", "readability_contrast",
                    "keyboard_focus", "clipping", "busy_disabled_error_states",
                    "density", "ux_verdict", "notes",
                ])
                for width in sizes:
                    for stage in stages:
                        for appearance in APPEARANCES:
                            writer.writerow([width, stage, appearance] + [""] * 7)
            checklist.chmod(0o600)
        return manifest
    finally:
        os.umask(old_umask)



# Explicit operator observations, not deductions from screenshots or CI.
# The private checklist stays on the authorized GTK host: only SHA-bound,
# bounded numeric counts may enter the private receipt.
REVIEW_COLUMNS = (
    "width", "stage", "appearance", "readability_contrast",
    "keyboard_focus", "clipping", "busy_disabled_error_states",
    "density", "ux_verdict", "notes",
)
REVIEW_AXES = REVIEW_COLUMNS[3:8]


def verify_completed_review(
    root: Path, *, sizes: dict[int, tuple[int, int]] = SIZES,
    stages: tuple[str, ...] = STAGES, expected_sha: str | None = None,
) -> dict[str, Any]:
    """Verify an explicitly filled, private UX checklist against original PNGs.

    Never certify a UI from a test PASS or a generated contact sheet. An OK
    value is an operator's judgment; N/A is always a documented exclusion.
    Any missing/duplicate row, stale commit or changed image fails closed.
    """
    root = root.resolve(strict=True)
    revisions = set()
    verified = {}
    for width, size in sizes.items():
        sha, _images = verify_capture_folder(
            root / f"modern-light-{width}", width=width, size=size,
            stages=stages,
        )
        revisions.add(sha)
        verified[width] = _images
    if len(revisions) != 1:
        raise ValueError("Captures de HEAD différents")
    sha = next(iter(revisions))
    if expected_sha is not None and sha != expected_sha:
        raise ValueError("HEAD différent des captures privées")
    output = root / "modern-light-private-review"
    if output.is_symlink() or not output.is_dir():
        raise ValueError("Répertoire de revue privée absent ou non sûr")
    manifest_path = _private_destination(output, "manifest.json")
    checklist_path = _private_destination(output, "ux-checklist.csv")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("status") != "VISUAL_REVIEW_REQUIRED"
            or manifest.get("git_sha") != sha
            or manifest.get("widths") != sorted(sizes)
            or manifest.get("verified_original_captures") != (
                2 * len(stages) * len(sizes)
            )):
        raise ValueError("Manifeste de revue différent des captures")
    if manifest.get("capture_fingerprint_sha256") != _capture_fingerprint(verified):
        raise ValueError("Captures différentes sur le même HEAD : grille UX périmée")
    # Bound the entire CSV before parsing; notes remain private and cannot
    # contaminate logs, GitHub comments or the verdict receipt.
    if checklist_path.stat().st_size > 256 * 1024:
        raise ValueError("Grille de revue trop volumineuse")
    raw = checklist_path.read_bytes()
    with checklist_path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != REVIEW_COLUMNS:
            raise ValueError("Colonnes de grille de revue incohérentes")
        rows = list(reader)
    expected = {
        (str(width), stage, mode)
        for width in sizes for stage in stages for mode in APPEARANCES
    }
    seen = set()
    findings = exclusions = 0
    for row in rows:
        key = (row["width"], row["stage"], row["appearance"])
        if key not in expected or key in seen:
            raise ValueError("Ligne de revue absente, répétée ou inconnue")
        seen.add(key)
        axes = [row[name] for name in REVIEW_AXES]
        if any(value not in {"OK", "KO", "N/A"} for value in axes):
            raise ValueError("Revue visuelle incomplète ou valeur non reconnue")
        if row["ux_verdict"] not in {"OK", "KO"}:
            raise ValueError("Verdict visuel manquant")
        if "N/A" in axes:
            exclusions += 1
            if not (row["notes"] or "").strip():
                raise ValueError("Exclusion visuelle sans justification")
        if "KO" in axes and row["ux_verdict"] != "KO":
            raise ValueError("Anomalie UX masquée par un verdict OK")
        if row["ux_verdict"] == "KO":
            findings += 1
    if seen != expected:
        raise ValueError("Revue visuelle incomplète")
    receipt = {
        "status": "CHECKLIST_VERIFIED",
        "git_sha": sha,
        "capture_fingerprint_sha256": manifest["capture_fingerprint_sha256"],
        "checklist_sha256": hashlib.sha256(raw).hexdigest(),
        "reviewed_rows": len(seen),
        "findings": findings,
        "rows_with_exclusions": exclusions,
        # Explicit operator records may be sufficient for subsequent UX
        # review, but this receipt itself neither approves GitHub nor closes
        # the ticket.
        "ready_for_ux_decision": findings == 0 and exclusions == 0,
    }
    destination = _private_destination(output, "visual-review-verdict.json")
    old_umask = os.umask(0o077)
    try:
        destination.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        destination.chmod(0o600)
    finally:
        os.umask(old_umask)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("e2e-artifacts"))
    parser.add_argument(
        "--verify-checklist", action="store_true",
        help="Contrôler la grille remplie contre les PNG originaux du HEAD, sans la publier",
    )
    args = parser.parse_args()
    try:
        current_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        if args.verify_checklist:
            verdict = verify_completed_review(args.root, expected_sha=current_sha)
            print(
                f"#757 : {verdict['reviewed_rows']} vues relues, "
                f"{verdict['findings']} anomalies, "
                f"{verdict['rows_with_exclusions']} exclusions ; "
                "résultat privé, décision UX finale distincte."
            )
            return 0
        report = build_review(args.root, expected_sha=current_sha)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        # No arbitrary source path, screenshot content or private report text
        # is printed into public CI logs.
        print(f"Revue #757 indisponible : {type(exc).__name__}")
        return 1
    print(
        f"#757 : {report['verified_original_captures']} PNG vérifiés, "
        f"{sum(len(x) for x in report['contact_sheets'].values())} planches "
        "privées ; verdict UX humain/agent requis."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
