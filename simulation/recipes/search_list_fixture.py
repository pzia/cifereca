#!/usr/bin/env python3
"""Remplace la bibliothèque d'un profil par 12 EPUB de stress pour la liste Recherche."""
from __future__ import annotations

import argparse
from dataclasses import replace
import html
import io
import json
from pathlib import Path
import sys
import zipfile

SIMULATION = Path(__file__).resolve().parents[1]
if str(SIMULATION) not in sys.path:
    sys.path.insert(0, str(SIMULATION))

from common.fake_books import FakeBookFactory, epub_bytes, slugify
from datasetctl import load_profile, profile_runtime


CASES = (
    {
        "id": "minimal",
        "title": "UXSTRESS minimal",
        "authors": (),
        "expect": "minimum de facettes produites par une entrée locale",
    },
    {
        "id": "long-title",
        "title": "UXSTRESS " + "Titre très long avec des mots et des séparateurs " * 7,
        "authors": ("Alice Courte",),
        "expect": "titre limité à deux lignes sans élargir la cellule",
    },
    {
        "id": "unbroken-title",
        "title": "UXSTRESS" + "X" * 180,
        "authors": ("Bastien Court",),
        "expect": "titre sans espaces tronqué/retourné sans débordement horizontal",
    },
    {
        "id": "long-author",
        "title": "UXSTRESS auteur unique très long",
        "authors": (
            "AuteurAvecUnNomExceptionnellementLongSansEspaces" + "Z" * 120,
        ),
        "expect": "facette auteur bornée avec contenu complet en tooltip",
    },
    {
        "id": "many-authors",
        "title": "UXSTRESS dix auteurs",
        "authors": tuple(f"Auteur {index:02d} NomDeFamille" for index in range(1, 11)),
        "expect": "10 facettes auteur + forme/source sans modifier la largeur de liste",
    },
    {
        "id": "many-long-authors",
        "title": "UXSTRESS dix auteurs longs",
        "authors": tuple(
            f"Auteur {index:02d} NomDeFamilleTrèsLongPourTesterLesFacettes"
            for index in range(1, 11)
        ),
        "expect": "plusieurs lignes de facettes restent contenues dans la cellule",
    },
    {
        "id": "unicode",
        "title": "UXSTRESS caractères Unicode — été, œuvre, Straße, 東京",
        "authors": ("Éléonore d’Aubigné-Longchamp", "山田 太郎"),
        "expect": "Unicode sans artefact de largeur ni perte de contenu",
    },
    {
        "id": "boundary-24",
        "title": "UXSTRESS borne facette 24",
        "authors": ("ABCDEFGHIJKLMNOPQRSTUVWX",),
        "expect": "valeur à la borne commune non tronquée",
    },
    {
        "id": "boundary-25",
        "title": "UXSTRESS borne facette 25",
        "authors": ("ABCDEFGHIJKLMNOPQRSTUVWXY",),
        "expect": "valeur juste au-dessus de la borne tronquée avec ellipse",
    },
    {
        "id": "mixed-extreme",
        "title": "UXSTRESS " + "Combinaison titre long " * 10,
        "authors": tuple(
            f"Coauteur {index:02d} — Patronyme extrêmement développé"
            for index in range(1, 11)
        ),
        "expect": "titre + nombreuses facettes sans décalage du bord gauche",
    },
    {
        "id": "punctuation",
        "title": "UXSTRESS — ponctuation / parenthèses [crochets] : deux-points",
        "authors": ("Nom, Prénom ; avec ponctuation / secondaire",),
        "expect": "ponctuation longue sans casser le FlowBox",
    },
    {
        "id": "control",
        "title": "UXSTRESS contrôle normal",
        "authors": ("Zoé Martin",),
        "expect": "ligne témoin de densité normale",
    },
)


def _patch_creators(payload: bytes, authors: tuple[str, ...]) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(payload), "r")
    out = io.BytesIO()
    with src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "OEBPS/content.opf":
                text = data.decode("utf-8")
                start = text.find("<dc:creator>")
                end = text.find("</dc:creator>", start)
                if start < 0 or end < 0:
                    raise RuntimeError("dc:creator introuvable dans l'EPUB de base")
                end += len("</dc:creator>")
                creators = "".join(
                    f"<dc:creator>{html.escape(author)}</dc:creator>"
                    for author in authors
                )
                text = text[:start] + creators + text[end:]
                data = text.encode("utf-8")
            dst.writestr(info, data)
    return out.getvalue()


def build(profile_name: str) -> dict:
    _path, profile = load_profile(profile_name)
    library_cfg = profile.get("library") or {}
    expected = int(library_cfg.get("epubs") or 0)
    if expected != len(CASES):
        raise RuntimeError(
            f"{profile_name}: library.epubs={expected}, fixture={len(CASES)}"
        )

    target = (
        profile_runtime(profile)
        / "library"
        / str(library_cfg.get("identity_dir") or "ux")
        / str(library_cfg.get("provider_dir") or "local")
    )
    target.mkdir(parents=True, exist_ok=True)
    for old in target.glob("*.epub"):
        old.unlink()

    factory = FakeBookFactory(int(library_cfg.get("seed") or 20261008), "ux-search-list")
    created = []
    for index, case in enumerate(CASES, 1):
        base = factory.make(index)
        primary_author = case["authors"][0] if case["authors"] else ""
        book = replace(base, title=case["title"], author=primary_author)
        payload = _patch_creators(epub_bytes(book), tuple(case["authors"]))
        path = target / f"{index:02d}-{slugify(case['id'])}.epub"
        path.write_bytes(payload)
        created.append(
            {
                "id": case["id"],
                "path": str(path),
                "title": case["title"],
                "authors": list(case["authors"]),
                "expect": case["expect"],
            }
        )

    manifest = {
        "profile": profile_name,
        "query": "UXSTRESS",
        "count": len(created),
        "target": str(target),
        "cases": created,
    }
    manifest_path = profile_runtime(profile) / "search-list-fixture.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="ux-search-list")
    args = parser.parse_args()
    build(args.profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
