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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("e2e-artifacts"))
    args = parser.parse_args()
    try:
        current_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        report = build_review(args.root, expected_sha=current_sha)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
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
