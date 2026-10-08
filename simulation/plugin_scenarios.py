#!/usr/bin/env python3
"""Scénarios déterministes de plugins, hors ligne et sans comptes utilisateurs.

Ce générateur prépare des JDD, pas des réponses HTTP ni des screenshots GTK.
Les fixtures sont isolées sous --out ; aucun fichier du profil réel n'est touché.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import html
import json
from pathlib import Path

from common.fake_books import FakeBookFactory, epub_bytes

SEED = 20261008
ISBN_A = "9780306406157"
ISBN_B = "9780140328721"
ISBN_C = "9780553386790"
HOST = "https://ux-fixtures.invalid"


def youth_records():
    def book(n, title, isbn="", ages=(), *, author="Camille Exemple", **extra):
        return {
            "record_id": f"youth-{n}",
            "source_item_id": str(n),
            "title": title,
            "authors": [author],
            "identifiers": {"isbn": isbn} if isbn else {},
            "notice_url": f"{HOST}/books/{n}",
            "language": "fr",
            "formats": ["epub"],
            "themes": ["aventure", "découverte"],
            "age_ranges": list(ages),
            **extra,
        }
    return {
        "version": 1, "synthetic": True, "source_id": "ux-youth",
        "secondary_source_id": "ux-youth-second",
        "identities": ["ux-camille", "ux-alexandre"],
        "cases": {
            "reading_vs_interest": [1],
            "unknown": [3],
            "inferred_opt_in": [4],
            "edition_conflict": [1, 5],
            "source_conflict": [1, 6],
            "age_boundary_17": [7],
            "age_boundary_18_no_match": [],
            "empty_source": [],
        },
        "records": [
            book(1, "L'Atlas des lucioles", ISBN_A, [
                {"age_min": 8, "age_max": 11, "age_kind": "interest", "source_label": "Interest age 8-11"},
                {"age_min": 10, "age_max": 12, "age_kind": "reading", "source_label": "Reading age 10-12"},
            ]),
            book(2, "Le Jardin des mots", ISBN_B, [
                {"age_min": 0, "age_max": 6, "age_kind": "recommended", "source_label": "jusqu'à 6 ans"},
            ]),
            book(3, "Le Passage sans âge"),
            book(4, "La Carte supposée", ages=[
                {"age_min": 11, "age_max": 13, "age_kind": "inferred",
                 "inferred": True, "confidence": 0.55, "source_label": "inférence, NON attestée"},
            ]),
            book(5, "L'Atlas des lucioles", ISBN_C, [
                {"age_min": 13, "age_max": 15, "age_kind": "publisher", "source_label": "13–15 ans"},
            ]),
            book(6, "L'Atlas des lucioles", ISBN_A, [
                {"age_min": 6, "age_max": 7, "age_kind": "recommended", "source_label": "6–7 ans"},
            ]),
            book(7, "Le Dernier Rivage", ages=[
                {"age_min": 17, "age_max": 17, "age_kind": "recommended", "source_label": "17 ans"},
            ]),
        ],
    }


def criticism_records():
    rows = [
        {"title": "L'Atlas des lucioles : lecture critique", "url": HOST + "/reviews/1",
         "critic_author": "Julie Lectrice", "published_at": "2026-10-01", "language": "fr",
         "excerpt": "Les liens entre les personnages se construisent sans effet facile.",
         "book": {"title": "L'Atlas des lucioles", "authors": ["Camille Exemple"], "isbns": [ISBN_A]}},
        {"title": "Une autre édition du même titre", "url": HOST + "/reviews/2",
         "critic_author": "Alex Reviewer", "published_at": "2026-09-12", "language": "en",
         "excerpt": "Vérifier l'édition avant d'associer la critique.",
         "book": {"title": "L'Atlas des lucioles", "authors": ["Camille Exemple"], "isbns": [ISBN_C]}},
        {"title": "Notice éditoriale non rattachée", "url": HOST + "/reviews/3",
         "critic_author": "Marc Observateur", "published_at": "2026-08-22", "language": "fr",
         "excerpt": "La provenance n'indique pas le livre concerné.", "book": {}},
    ]
    return {"version": 1, "synthetic": True, "sources": {
        "ux-critique-a": {"items": rows, "next_cursor": "", "fresh_for_seconds": 3600},
        "ux-critique-b": {"items": [dict(rows[0])], "next_cursor": "", "fresh_for_seconds": 3600},
        "ux-critique-empty": {"items": [], "next_cursor": "", "fresh_for_seconds": 3600},
        "ux-critique-error": {"error": "timeout-simule", "items": []},
    }, "book": {"title": "L'Atlas des lucioles", "authors": ["Camille Exemple"],
                "identifiers": {"isbn": ISBN_A}}}


def criticism_rss():
    ns = "https://cifereca.org/ns/book/1"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:book="{ns}"><channel>
<title>Critiques fictives UX</title><language>fr</language>
<item><title>L'Atlas des lucioles : lecture critique</title>
<link>{HOST}/reviews/1</link>
<description>Critique synthétique de démonstration.</description>
<dc:identifier>urn:isbn:{ISBN_A}</dc:identifier>
<book:title>L'Atlas des lucioles</book:title>
<book:author>Camille Exemple</book:author><book:notice>{HOST}/books/1</book:notice>
</item>
<item><title>Chronique sans livre identifié</title><link>{HOST}/reviews/empty</link>
<description>Aucune métadonnée bibliographique attestée.</description></item>
</channel></rss>
"""


def criticism_jsonfeed():
    return {
        "version": "https://jsonfeed.org/version/1.1",
        "title": "Critiques fictives UX",
        "language": "fr",
        "items": [{
            "id": HOST + "/reviews/1", "url": HOST + "/reviews/1",
            "title": "L'Atlas des lucioles : lecture critique",
            "authors": [{"name": "Julie Lectrice"}],
            "date_published": "2026-10-01T12:00:00Z",
            "summary": "Critique synthétique, jamais un article intégral.",
            "_cifereca_book": {"title": "L'Atlas des lucioles",
                               "authors": ["Camille Exemple"], "isbns": [ISBN_A]},
        }],
    }


def constellations_records():
    rows = []
    modes = ["owned", "borrowable", "free", "external"]
    for i in range(10):
        common = i < 7
        metadata = {
            "subjects": ["exploration", "cartes"] if common else ["astronomie"],
            "series": "Les Itinéraires" if i < 5 else "",
            "mood": ["serein"] if common else ["inquiétant"],
            "mood_inferred": i == 5,
            "language": "fr",
            "length_pages": 90 + i * 45,
            "availability": modes[i % len(modes)],
        }
        rows.append({
            "book_id": f"ux-constellation-{i}",
            "title": f"Les itinéraires imaginaires — volume {i + 1}",
            "authors": ["Camille Exemple" if i < 5 else "Alexandre Exemple"],
            "identifiers": {"provider": "ux-constellation", "source_item_id": f"book-{i}"},
            "metadata": metadata,
        })
    return {"version": 1, "synthetic": True,
            "identities": {"ux-camille": list(range(9)), "ux-alexandre": [2, 6, 9]},
            "scenarios": ["neighbours", "no-relations", "comfort", "explorer",
                          "project", "mood-inferred", "walk-4-to-8", "cross-identity"],
            "records": rows}


def create(out: Path, *, seed: int = SEED) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    books = FakeBookFactory(seed=seed, namespace="ux-specialized")
    fake = books.make(1)
    contents = {
        "youth.json": youth_records(),
        "criticism.json": criticism_records(),
        "criticism.jsonfeed.json": criticism_jsonfeed(),
        "constellations.json": constellations_records(),
    }
    for name, payload in contents.items():
        (out / name).write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (out / "criticism.rss.xml").write_text(criticism_rss(), encoding="utf-8")
    (out / "optimizer-valid.epub").write_bytes(epub_bytes(fake))
    (out / "optimizer-corrupt.epub").write_bytes(b"this is not a valid EPUB file\n")
    paths = sorted(p for p in out.iterdir() if p.is_file() and p.name != "manifest.json")
    manifest = {"version": 1, "synthetic": True, "seed": seed,
                "scope": ["jeunesse", "critique-litteraire", "constellations", "epub-optimizer"],
                "files": {p.name: sha256(p.read_bytes()).hexdigest() for p in paths},
                "warning": "Fixtures de contrat hors ligne ; pas encore de service HTTP ni E2E GTK."}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    print(json.dumps(create(args.out, seed=args.seed), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
