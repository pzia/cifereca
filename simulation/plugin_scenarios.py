#!/usr/bin/env python3
"""Scénarios déterministes de plugins, hors ligne et sans comptes utilisateurs.

Ce générateur prépare des JDD, pas des réponses HTTP ni des screenshots GTK.
Les fixtures sont isolées sous --out ; aucun fichier du profil réel n'est touché.
"""
from __future__ import annotations

import argparse
import io
import zipfile
from hashlib import sha256
import io
import json
import zipfile
from pathlib import Path

from common.fake_books import FakeBookFactory, epub_bytes

SEED = 20261008
SCHEMA_VERSION = 2
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



def youth_discovery_cases():
    """Requêtes fictives d'une métarecherche injectée, sans appels réseau.

    Les domaines .example sont des noms réservés à la documentation ; les
    scénarios de boucle locale, credentials et URL privées sont rejetés.
    """
    return {
        "version": 1, "synthetic": True, "query": "livres enfants",
        "search_results": [
            {"url": "https://lecture.example/books/atlas",
             "title": "Atlas", "snippet": "Interest age: 8-10"},
            {"url": "https://lecture.example/books/luciole",
             "title": "Luciole", "snippet": "Reading age: 7 to 9"},
            {"url": "https://petits.example/books/jardin",
             "title": "Jardin", "snippet": "Picture books for children"},
            {"url": "https://noise.example/press",
             "title": "Durées", "snippet": "Publication cycle: 18 months"},
            {"url": "http://127.0.0.1/private", "title": "Privé",
             "snippet": "Reading age: 6 to 8"},
            {"url": "https://user:password@credential.example/book", "title": "Secret",
             "snippet": "Age: 10+"},
            {"url": "https://lecture.example/books/atlas",
             "title": "Doublon", "snippet": "Interest age: 8-10"},
        ],
        "fetch_pages": {
            "https://lecture.example/books/atlas": {
                "content_type": "text/html", "text": "<p>Interest age: 8-10</p>"},
            "https://lecture.example/books/luciole": {
                "content_type": "text/html", "text": "<p>Reading age: 7 to 9</p>"},
            "https://petits.example/books/jardin": {
                "content_type": "text/html",
                "text": "<meta name='description' content='Age: 18+ months'><p>Baby books</p>"},
        },
        "fail_fetch_urls": ["https://noise.example/press"],
        "expected_domains": ["lecture.example", "petits.example"],
        "never_register_automatically": True,
        "manual_review_required": True,
    }


def youth_enrichment_cases():
    """Relations bibliographiques, preuves et identités; aucun cache réel touché."""
    return {
        "version": 1, "synthetic": True,
        "identities": ["ux-camille", "ux-alexandre"],
        "source_records": [1, 2, 3, 4, 5, 6, 7],
        "cases": {
            "isbn_exact": {
                "book_id": "ux-enrichment-exact", "title": "Un titre local différent",
                "identifiers": {"isbn": ISBN_B}, "identity_id": "ux-camille",
                "expected_record": "youth-2",
                "expected_match": "isbn:" + ISBN_B,
            },
            "edition_conflict": {
                "book_id": "ux-enrichment-conflict",
                "title": "L'Atlas des lucioles",
                "identifiers": {"isbn": ISBN_C}, "identity_id": "ux-camille",
                "expected_record": "youth-5",
                "expected_match": "isbn:" + ISBN_C,
            },
            "ambiguous_isbn": {
                "book_id": "ux-enrichment-ambiguous",
                "title": "L'Atlas des lucioles",
                "identifiers": {"isbn": ISBN_A}, "identity_id": "ux-camille",
                "expected_record": None,
            },
            "title_only": {
                "book_id": "ux-enrichment-title-only",
                "title": "L'Atlas des lucioles",
                "identifiers": {}, "identity_id": "ux-camille",
                "expected_record": None,
            },
            "wrong_identity": {
                "book_id": "ux-enrichment-other-identity",
                "title": "Le Jardin des mots",
                "identifiers": {"isbn": ISBN_B}, "identity_id": "ux-alexandre",
                "expected_record": None,
            },
            "missing_identity": {
                "book_id": "ux-enrichment-no-identity",
                "title": "Le Jardin des mots",
                "identifiers": {"isbn": ISBN_B}, "identity_id": "",
                "expected_record": None,
            },
        },
        "cache_scope": {"ux-camille": [1, 2, 3, 4, 5, 6, 7], "ux-alexandre": []},
        "requires_strong_identifier": True,
        "never_fetch_network": True,
    }


def reproducible_epub(payload: bytes) -> bytes:
    """Neutraliser les timestamps ZIP volatils sans changer le contenu EPUB.

    Les fichiers EPUB sont des ZIP : ZipFile.writestr peut utiliser l'heure
    courante lors de l'insertion. Une seed fixe ne suffit donc pas.
    """
    fixed = (1980, 1, 1, 0, 0, 0)
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(payload), "r") as original:
        with zipfile.ZipFile(output, "w") as target:
            for member in original.infolist():
                entry = zipfile.ZipInfo(member.filename, date_time=fixed)
                entry.compress_type = member.compress_type
                entry.external_attr = member.external_attr
                entry.create_system = member.create_system
                target.writestr(entry, original.read(member.filename))
    return output.getvalue()


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
        "youth-discovery.json": youth_discovery_cases(),
        "youth-enrichment.json": youth_enrichment_cases(),
    }
    for name, payload in contents.items():
        (out / name).write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (out / "criticism.rss.xml").write_text(criticism_rss(), encoding="utf-8")
    (out / "optimizer-valid.epub").write_bytes(reproducible_epub(epub_bytes(fake)))
    (out / "optimizer-corrupt.epub").write_bytes(b"this is not a valid EPUB file\n")
    paths = sorted(p for p in out.iterdir() if p.is_file() and p.name != "manifest.json")
    manifest = {"version": SCHEMA_VERSION, "synthetic": True, "seed": seed,
                "scope": ["jeunesse", "critique-litteraire", "constellations", "epub-optimizer",
                          "youth-discovery", "youth-detail-enrichment"],
                "files": {p.name: sha256(p.read_bytes()).hexdigest() for p in paths},
                "warning": "JDD de contrat isolés ; activation explicite, aucun E2E GTK prouvé."}
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
