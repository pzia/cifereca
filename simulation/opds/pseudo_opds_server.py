#!/usr/bin/env python3
"""
pseudo_opds_server.py — faux serveur OPDS HTTP pour tests Cifereca.

Sources OPDS simulées d'après les plugins Cifereca :
  - Gallica
  - Project Gutenberg

Au premier lancement, le serveur génère par défaut 10 000 notices fictives par
source dans un cache SQLite persistant. Les couvertures PNG et les EPUB sont
générés à la demande, puis mis en cache sur disque.

Exemples :
    python3 pseudo_opds_server.py
    python3 pseudo_opds_server.py --bind 0.0.0.0 --port 8080
    python3 pseudo_opds_server.py --rebuild
    python3 pseudo_opds_server.py --books-per-source 100 --build-cache-only

Configuration Cifereca utile :
    Gallica:
      opds_url       = http://127.0.0.1:8080/gallica/opds
      epub_search_url= http://127.0.0.1:8080/gallica/services/engine/search/opds

    Gutenberg:
      opds_search_url= http://127.0.0.1:8080/gutenberg/ebooks/search.opds/

Dépendances : Python 3, bibliothèque standard uniquement.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import random
import re
import shutil
import sqlite3
import sys
import threading
import unicodedata
import zipfile
from datetime import date, timedelta, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit

DATASET_ROOT = Path(__file__).resolve().parents[1]
if str(DATASET_ROOT) not in sys.path:
    sys.path.insert(0, str(DATASET_ROOT))

from common.fake_books import FakeBook, FakeBookFactory, cover_png_bytes, write_epub as write_shared_epub

APP_VERSION = "1.2"
CACHE_SCHEMA_VERSION = "1"
DEFAULT_BOOKS_PER_SOURCE = 10_000
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
SOURCES = ("gallica", "gutenberg")

ATOM = "http://www.w3.org/2005/Atom"
DC = "http://purl.org/dc/elements/1.1/"
DCTERMS = "http://purl.org/dc/terms/"
OPENSEARCH = "http://a9.com/-/spec/opensearch/1.1/"

FIRST_NAMES = [
    "Adèle", "Alice", "Bastien", "Camille", "Diane", "Élias", "Émile",
    "Inès", "Jules", "Lina", "Maël", "Nora", "Oscar", "Salomé", "Théo",
    "Victor", "Yasmine", "Zoé", "Lucien", "Madeleine", "Auguste", "Céleste",
]
LAST_NAMES = [
    "Armand", "Beaulieu", "Caron", "Delmas", "Faure", "Garnier", "Hébert",
    "Lefèvre", "Moreau", "Perrin", "Roche", "Valette", "Vidal", "Bernier",
    "Clément", "Dumont", "Fournier", "Girard", "Laurent", "Marchand",
]
PLACES = [
    "la baie des Brumes", "le quartier des Verrières", "la vallée d'Argile",
    "l'île aux Lanternes", "la gare de Minuit", "les collines de Verre",
    "le port des Écluses", "la forêt de Cendre", "la ville des Horloges",
    "le jardin des Amandiers", "le quai des Ombres", "la maison du Phare",
    "la rue des Cartographes", "le passage des Étoiles", "la plaine d'Azur",
    "le village des Saules", "la tour des Vents", "le lac des Silences",
]
OBJECTS = [
    "carnet", "miroir", "violon", "phare", "passage", "jardin", "train",
    "secret", "atelier", "silence", "horloge", "clef", "boussole", "portrait",
    "manuscrit", "lanterne", "carte", "valise", "médaillon", "livre",
]
QUALIFIERS = [
    "oublié", "silencieux", "invisible", "bleu", "d'hiver", "de minuit",
    "des brumes", "des jours perdus", "aux reflets d'or", "du dernier été",
    "de verre", "des marées", "des voyageurs", "des secrets", "du nord",
]
SUBJECTS = [
    "Aventure", "Mystère", "Voyage", "Histoire", "Fantastique", "Poésie",
    "Science", "Mer", "Famille", "Mémoire", "Nature", "Enquête", "Art",
    "Musique", "Correspondance", "Exploration", "Philosophie", "Société",
]
LANGUAGES = ["fr", "fr", "fr", "fr", "en", "de", "es", "it"]
TITLE_PATTERNS = [
    "Le {object} {qualifier} de {place}",
    "Le {object} de {place}, {qualifier}",
    "La mémoire {qualifier} de {place}",
    "Les nuits {qualifier} de {place}",
    "Sous le ciel {qualifier} de {place}",
    "Le secret du {object} de {place}",
    "Une saison {qualifier} à {place}",
    "Chroniques du {object} de {place}",
    "Le dernier {object} de {place}",
    "L'ombre du {object} sur {place}",
]
PALETTES = [
    ("#183153", "#f4d35e", "#ffffff"),
    ("#3b1f47", "#e07a5f", "#f4f1de"),
    ("#143642", "#ec9a29", "#ffffff"),
    ("#4f2d3f", "#d9b44a", "#f7f3e8"),
    ("#1d3557", "#e63946", "#f1faee"),
    ("#264653", "#e9c46a", "#f4f1de"),
    ("#293241", "#ee6c4d", "#e0fbfc"),
    ("#2b2d42", "#ef233c", "#edf2f4"),
]


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def stable_int(*parts: object) -> int:
    digest = hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def source_seed(global_seed: int, source: str, *parts: object) -> int:
    return stable_int(global_seed, source, *parts)


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower() or "book"


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_title(rng: random.Random, used: set[str]) -> tuple[str, str, str]:
    for _ in range(100):
        place = rng.choice(PLACES)
        obj = rng.choice(OBJECTS)
        title = rng.choice(TITLE_PATTERNS).format(
            object=obj,
            place=place,
            qualifier=rng.choice(QUALIFIERS),
        )
        if title not in used:
            used.add(title)
            return title, place, obj
    # Le suffixe ne sert qu'en cas de collision extrême.
    n = len(used) + 1
    title = f"Carnet fictif n° {n:05d}"
    used.add(title)
    return title, rng.choice(PLACES), rng.choice(OBJECTS)


def make_summary(title: str, place: str, subject: str, source: str) -> str:
    source_name = "Gallica" if source == "gallica" else "Project Gutenberg"
    return (
        f"Notice fictive de test pour {source_name}. Dans {place}, « {title} » "
        f"mêle {subject.lower()}, découverte et mémoire. Cette ressource n'est pas "
        "un ouvrage réel et sert uniquement aux tests Cifereca."
    )


def generate_records(source: str, count: int, seed: int):
    rng = random.Random(source_seed(seed, source, "service"))
    factory = FakeBookFactory(seed, f"opds-{source}")
    if source == "gallica":
        first_date = date(1850, 1, 1)
        span_days = (date(1938, 12, 31) - first_date).days
        publisher_pool = [
            "Éditions du Passage", "Imprimerie des Arcades", "Maison Sillage",
            "Librairie du Rivage", "Atelier des Lettres", "Presses du Nord",
        ]
    else:
        first_date = date(1780, 1, 1)
        span_days = (date(1928, 12, 31) - first_date).days
        publisher_pool = [
            "Project Gutenberg Fictional Press", "Open Classics House",
            "Public Domain Editions", "Archive Lantern Press", "Free Text Society",
        ]

    for book_id in range(1, count + 1):
        book = factory.make(book_id)
        published = first_date + timedelta(days=rng.randrange(span_days + 1))
        publisher = rng.choice(publisher_pool)
        isbn = f"978{source_seed(seed, source, book_id) % 10_000_000_000:010d}"
        cover_variant = rng.randrange(len(PALETTES))
        yield (
            source, book_id, book.title, book.author, publisher, published.isoformat(),
            book.language, book.subject,
            make_summary(book.title, book.place, book.subject, source), isbn,
            book.place, book.object_name, cover_variant,
        )


class DatasetCache:
    def __init__(self, cache_dir: Path, books_per_source: int, seed: int, rebuild: bool = False):
        self.cache_dir = cache_dir
        self.books_per_source = books_per_source
        self.seed = seed
        self.db_path = cache_dir / "catalog.sqlite3"
        self.epub_dir = cache_dir / "epub"
        self.cover_dir = cache_dir / "covers"
        self._local = threading.local()

        if rebuild and cache_dir.exists():
            shutil.rmtree(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.epub_dir.mkdir(parents=True, exist_ok=True)
        self.cover_dir.mkdir(parents=True, exist_ok=True)
        self._prepare()

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def _prepare(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS books (
                source TEXT NOT NULL,
                book_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                publisher TEXT NOT NULL,
                published TEXT NOT NULL,
                language TEXT NOT NULL,
                subject TEXT NOT NULL,
                summary TEXT NOT NULL,
                isbn TEXT NOT NULL,
                place TEXT NOT NULL,
                object_name TEXT NOT NULL,
                cover_variant INTEGER NOT NULL,
                PRIMARY KEY(source, book_id)
            );
            CREATE INDEX IF NOT EXISTS idx_books_source_title ON books(source, title);
            CREATE INDEX IF NOT EXISTS idx_books_source_author ON books(source, author);
            CREATE INDEX IF NOT EXISTS idx_books_source_subject ON books(source, subject);
            CREATE INDEX IF NOT EXISTS idx_books_source_isbn ON books(source, isbn);
            """
        )
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        counts = {
            row[0]: row[1]
            for row in conn.execute("SELECT source, COUNT(*) FROM books GROUP BY source")
        }
        valid = (
            meta.get("schema_version") == CACHE_SCHEMA_VERSION
            and meta.get("app_version") == APP_VERSION
            and meta.get("books_per_source") == str(self.books_per_source)
            and meta.get("seed") == str(self.seed)
            and all(counts.get(source) == self.books_per_source for source in SOURCES)
        )

        if valid:
            print(f"[cache] Réutilisation de {self.db_path} ({sum(counts.values())} notices)")
            conn.close()
            return

        print(f"[cache] Génération de {self.books_per_source} notices × {len(SOURCES)} sources…")
        conn.execute("DELETE FROM books")
        conn.execute("DELETE FROM meta")
        for source in SOURCES:
            batch = []
            for record in generate_records(source, self.books_per_source, self.seed):
                batch.append(record)
                if len(batch) >= 1000:
                    conn.executemany(
                        "INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch
                    )
                    batch.clear()
            if batch:
                conn.executemany("INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
            conn.commit()
            print(f"[cache] {source}: {self.books_per_source} notices prêtes")

        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("schema_version", CACHE_SCHEMA_VERSION),
                ("app_version", APP_VERSION),
                ("books_per_source", str(self.books_per_source)),
                ("seed", str(self.seed)),
                ("generated_at", iso_now()),
            ],
        )
        conn.commit()
        conn.close()
        print(f"[cache] Catalogue prêt: {self.db_path}")

    def count(self, source: str) -> int:
        return self.connect().execute(
            "SELECT COUNT(*) FROM books WHERE source=?", (source,)
        ).fetchone()[0]

    def get(self, source: str, book_id: int):
        return self.connect().execute(
            "SELECT * FROM books WHERE source=? AND book_id=?", (source, book_id)
        ).fetchone()

    @staticmethod
    def _field_column(field: str) -> str:
        return {
            "title": "title",
            "author": "author",
            "subject": "subject",
            "isbn": "isbn",
            "language": "language",
        }.get(field, "")

    def search(self, source: str, query: str, offset: int, limit: int, field: str = "all"):
        conn = self.connect()
        limit = max(1, min(MAX_PAGE_SIZE, int(limit)))
        offset = max(0, int(offset))
        terms = [x for x in re.split(r"\s+", query.strip()) if x]
        params: list[object] = [source]
        where = ["source=?"]
        column = self._field_column(field)

        if terms:
            if column:
                expr = f"lower({column})"
            else:
                expr = "lower(title || ' ' || author || ' ' || subject || ' ' || publisher || ' ' || isbn)"
            for term in terms:
                where.append(expr + " LIKE ?")
                params.append("%" + term.casefold() + "%")

        sql_where = " AND ".join(where)
        total = conn.execute(
            f"SELECT COUNT(*) FROM books WHERE {sql_where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM books WHERE {sql_where} ORDER BY book_id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return total, rows

    def cover_path(self, source: str, book_id: int) -> Path:
        return self.cover_dir / source / f"{book_id}.png"

    def epub_path(self, source: str, book_id: int) -> Path:
        return self.epub_dir / source / f"{book_id}.epub"


class FakeOPDSApp:
    def __init__(self, cache: DatasetCache, *, broken_assets_every: int = 0):
        self.cache = cache
        self.broken_assets_every = max(0, int(broken_assets_every))

    def asset_is_broken(self, book_id: int) -> bool:
        return self.broken_assets_every > 0 and int(book_id) % self.broken_assets_every == 0

    @staticmethod
    def _base(handler: BaseHTTPRequestHandler) -> str:
        host = handler.headers.get("Host") or f"127.0.0.1:{handler.server.server_port}"
        proto = handler.headers.get("X-Forwarded-Proto", "http").split(",", 1)[0].strip() or "http"
        return f"{proto}://{host}"

    @staticmethod
    def _send(handler: BaseHTTPRequestHandler, status: int, content_type: str, body: bytes, *, cache_control: str = "no-cache"):
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", cache_control)
        handler.end_headers()
        handler.wfile.write(body)

    def send_text(self, handler, status, content_type, text, **kwargs):
        self._send(handler, status, content_type + "; charset=utf-8", text.encode("utf-8"), **kwargs)

    def send_json(self, handler, status, obj):
        self.send_text(handler, status, "application/json", json.dumps(obj, ensure_ascii=False, indent=2))

    def home(self, handler):
        base = self._base(handler)
        payload = {
            "name": "Cifereca fake OPDS server",
            "version": APP_VERSION,
            "books_per_source": self.cache.books_per_source,
            "sources": {
                "gallica": {
                    "root": base + "/gallica/opds",
                    "search": base + "/gallica/services/engine/search/opds",
                },
                "gutenberg": {
                    "search": base + "/gutenberg/ebooks/search.opds/",
                },
            },
            "health": base + "/health",
            "faults": {"broken_assets_every": self.broken_assets_every},
        }
        self.send_json(handler, HTTPStatus.OK, payload)

    def health(self, handler):
        self.send_json(handler, HTTPStatus.OK, {
            "ok": True,
            "version": APP_VERSION,
            "counts": {source: self.cache.count(source) for source in SOURCES},
        })

    def gallica_root(self, handler):
        base = self._base(handler)
        updated = iso_now()
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="{ATOM}">
  <id>{esc(base)}/gallica/opds</id>
  <title>Gallica — catalogue OPDS fictif Cifereca</title>
  <updated>{updated}</updated>
  <link rel="self" type="application/atom+xml;profile=opds-catalog" href="{esc(base)}/gallica/opds"/>
  <link rel="search" type="application/opensearchdescription+xml" href="{esc(base)}/gallica/opensearch.xml"/>
</feed>'''
        self.send_text(handler, HTTPStatus.OK, "application/atom+xml;profile=opds-catalog", xml)

    def gallica_opensearch(self, handler):
        base = self._base(handler)
        # Les accolades OpenSearch sont volontairement conservées dans template.
        template = (
            base
            + "/gallica/services/engine/search/opds?operation=searchRetrieve&amp;version=1.2"
            + "&amp;query={searchTerms}&amp;startRecord={startIndex?}&amp;maximumRecords={count?}"
        )
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<OpenSearchDescription xmlns="{OPENSEARCH}">
  <ShortName>Gallica fictif</ShortName>
  <Description>Catalogue OPDS fictif pour tests Cifereca</Description>
  <Url type="application/atom+xml;profile=opds-catalog" template="{template}"/>
</OpenSearchDescription>'''
        self.send_text(handler, HTTPStatus.OK, "application/opensearchdescription+xml", xml)

    @staticmethod
    def parse_gallica_query(raw: str) -> tuple[str, str]:
        raw = raw or ""
        field = "all"
        low = raw.casefold()
        if "dc.title" in low:
            field = "title"
        elif "dc.creator" in low:
            field = "author"
        elif "dc.subject" in low:
            field = "subject"
        elif "dc.identifier" in low:
            field = "isbn"

        quoted = re.findall(r'"((?:\\.|[^"\\])*)"', raw)
        useful = []
        for item in quoted:
            item = item.replace('\\"', '"').replace('\\\\', '\\').strip()
            if item.casefold() not in {"epub", "pdf"}:
                useful.append(item)
        if useful:
            return useful[0], field

        # Support pratique de query=foo hors CQL.
        cleaned = re.sub(r"\([^)]*formatspecific[^)]*\)", "", raw, flags=re.I)
        cleaned = re.sub(r"\b(?:gallica|dc\.[a-z]+)\s+all\b", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\b(?:and|or)\b", " ", cleaned, flags=re.I)
        cleaned = cleaned.strip(" ()'\"")
        return cleaned, field

    def gallica_search(self, handler, qs):
        query_raw = (qs.get("query") or qs.get("q") or [""])[0]
        query, field = self.parse_gallica_query(query_raw)
        start_record = max(1, int((qs.get("startRecord") or ["1"])[0] or 1))
        maximum = int((qs.get("maximumRecords") or [str(DEFAULT_PAGE_SIZE)])[0] or DEFAULT_PAGE_SIZE)
        maximum = max(1, min(MAX_PAGE_SIZE, maximum))
        offset = start_record - 1
        total, rows = self.cache.search("gallica", query, offset, maximum, field)
        self.send_atom_feed(handler, "gallica", query, rows, total, offset, maximum, gallica=True)

    def gutenberg_search(self, handler, qs):
        query = (qs.get("query") or qs.get("q") or [""])[0].strip()
        page = max(1, int((qs.get("page") or ["1"])[0] or 1))
        limit = DEFAULT_PAGE_SIZE
        offset = (page - 1) * limit
        language_codes = {
            "french": "fr", "english": "en", "german": "de", "spanish": "es",
            "italian": "it", "portuguese": "pt", "dutch": "nl",
        }
        folded = query.casefold()
        server_query = language_codes.get(folded, query)
        field = "language" if folded in language_codes else "all"
        total, rows = self.cache.search("gutenberg", server_query, offset, limit, field)
        self.send_atom_feed(handler, "gutenberg", query, rows, total, offset, limit, page=page)

    def send_atom_feed(self, handler, source: str, query: str, rows, total: int, offset: int, limit: int, *, gallica: bool = False, page: int | None = None):
        base = self._base(handler)
        source_label = "Gallica" if source == "gallica" else "Project Gutenberg"
        path = "/gallica/services/engine/search/opds" if gallica else "/gutenberg/ebooks/search.opds/"
        params = {"query": query}
        if gallica:
            params.update({"operation": "searchRetrieve", "version": "1.2", "startRecord": offset + 1, "maximumRecords": limit})
        else:
            params["page"] = page or (offset // limit + 1)
        self_url = base + path + "?" + urlencode(params)
        updated = iso_now()

        links = [f'<link rel="self" type="application/atom+xml;profile=opds-catalog" href="{esc(self_url)}"/>']
        if offset + limit < total:
            if gallica:
                next_params = dict(params)
                next_params["startRecord"] = offset + limit + 1
            else:
                next_params = {"query": query, "page": (page or 1) + 1}
            next_url = base + path + "?" + urlencode(next_params)
            links.append(f'<link rel="next" type="application/atom+xml;profile=opds-catalog" href="{esc(next_url)}"/>')

        entries = "\n".join(self.atom_entry(base, row) for row in rows)
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="{ATOM}" xmlns:dcterms="{DCTERMS}" xmlns:dc="{DC}" xmlns:opensearch="{OPENSEARCH}">
  <id>{esc(self_url)}</id>
  <title>{esc(source_label)} — résultats fictifs pour « {query or 'catalogue'} »</title>
  <updated>{updated}</updated>
  <opensearch:totalResults>{total}</opensearch:totalResults>
  <opensearch:startIndex>{offset + 1}</opensearch:startIndex>
  <opensearch:itemsPerPage>{limit}</opensearch:itemsPerPage>
  {' '.join(links)}
  {entries}
</feed>'''
        self.send_text(handler, HTTPStatus.OK, "application/atom+xml;profile=opds-catalog", xml)

    @staticmethod
    def atom_entry(base: str, row: sqlite3.Row) -> str:
        source = row["source"]
        book_id = row["book_id"]
        if source == "gallica":
            record_id = f"ark:/12148/fake{book_id:08d}"
            atom_id = f"https://gallica.bnf.fr/{record_id}"
        else:
            record_id = str(book_id)
            atom_id = f"urn:gutenberg:{book_id}"
        detail = f"{base}/{source}/books/{book_id}"
        is_pdf = source == "gallica" and int(book_id) % 2 == 0
        acquisition = f"{base}/{source}/pdf/{book_id}.pdf" if is_pdf else f"{base}/{source}/epub/{book_id}.epub"
        acquisition_type = "application/pdf" if is_pdf else "application/epub+zip"
        acquisition_label = "PDF" if is_pdf else "EPUB"
        cover = f"{base}/{source}/covers/{book_id}.png"
        updated = row["published"] + "T12:00:00Z"
        return f'''<entry>
    <id>{esc(atom_id)}</id>
    <title>{esc(row['title'])}</title>
    <updated>{updated}</updated>
    <published>{updated}</published>
    <author><name>{esc(row['author'])}</name></author>
    <summary type="text">{esc(row['summary'])}</summary>
    <dcterms:issued>{esc(row['published'])}</dcterms:issued>
    <dcterms:language>{esc(row['language'])}</dcterms:language>
    <dc:publisher>{esc(row['publisher'])}</dc:publisher>
    <dc:identifier>{esc(row['isbn'])}</dc:identifier>
    <category term="{esc(row['subject'])}" label="{esc(row['subject'])}"/>
    <link rel="alternate" type="text/html" href="{esc(detail)}"/>
    <link rel="http://opds-spec.org/acquisition/open-access" type="{acquisition_type}" href="{esc(acquisition)}" title="{acquisition_label}"/>
    <link rel="http://opds-spec.org/image/thumbnail" type="image/png" href="{esc(cover)}"/>
    <link rel="http://opds-spec.org/image" type="image/png" href="{esc(cover)}"/>
  </entry>'''

    def book_page(self, handler, source: str, book_id: int):
        row = self.cache.get(source, book_id)
        if row is None:
            return self.not_found(handler)
        base = self._base(handler)
        body = f'''<!doctype html><html lang="fr"><meta charset="utf-8"><title>{esc(row['title'])}</title>
<body><h1>{esc(row['title'])}</h1><p><b>{esc(row['author'])}</b> — {esc(row['publisher'])}, {esc(row['published'])}</p>
<p>{esc(row['summary'])}</p><p><a href="{esc(base)}/{source}/epub/{book_id}.epub">Télécharger l'EPUB fictif</a></p></body></html>'''
        self.send_text(handler, HTTPStatus.OK, "text/html", body)

    def cover(self, handler, source: str, book_id: int):
        row = self.cache.get(source, book_id)
        if row is None:
            return self.not_found(handler)
        if self.asset_is_broken(book_id):
            return self.send_json(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"error": "simulated cover failure"})
        path = self.cache.cover_path(source, book_id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(cover_png_bytes(self._book_from_row(row), width=480, height=720))
        self._send(handler, HTTPStatus.OK, "image/png", path.read_bytes(), cache_control="public, max-age=86400")

    def epub(self, handler, source: str, book_id: int):
        row = self.cache.get(source, book_id)
        if row is None:
            return self.not_found(handler)
        if self.asset_is_broken(book_id):
            return self.send_json(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"error": "simulated epub failure"})
        path = self.cache.epub_path(source, book_id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            self.write_epub(path, row)
        self._send(handler, HTTPStatus.OK, "application/epub+zip", path.read_bytes(), cache_control="public, max-age=86400")

    def pdf(self, handler, book_id: int):
        row = self.cache.get("gallica", book_id)
        if row is None:
            return self.not_found(handler)
        if self.asset_is_broken(book_id):
            return self.send_json(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"error": "simulated pdf failure"})
        body = (
            b"%PDF-1.4\n"
            b"% Cifereca fake Gallica PDF\n"
            b"1 0 obj<</Type/Catalog>>endobj\n"
            b"trailer<</Root 1 0 R>>\n"
            b"%%EOF\n"
        )
        self._send(handler, HTTPStatus.OK, "application/pdf", body, cache_control="public, max-age=86400")

    @staticmethod
    def make_cover_svg(row: sqlite3.Row) -> str:
        bg, accent, fg = PALETTES[int(row["cover_variant"]) % len(PALETTES)]
        words = row["title"].split()
        lines: list[str] = []
        current: list[str] = []
        for word in words:
            candidate = " ".join(current + [word])
            if len(candidate) <= 25:
                current.append(word)
            else:
                if current:
                    lines.append(" ".join(current))
                current = [word]
        if current:
            lines.append(" ".join(current))
        lines = lines[:4]
        title_nodes = "\n".join(
            f'<text x="600" y="{700 + i*95}" text-anchor="middle" font-size="68" font-family="serif" font-weight="700" fill="{fg}">{esc(line)}</text>'
            for i, line in enumerate(lines)
        )
        source_label = "GALLICA — TEST" if row["source"] == "gallica" else "GUTENBERG — TEST"
        return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1600" viewBox="0 0 1200 1600">
<rect width="1200" height="1600" fill="{bg}"/><circle cx="930" cy="260" r="210" fill="{accent}" opacity=".92"/>
<rect x="120" y="180" width="18" height="1180" fill="{accent}"/>
<text x="600" y="250" text-anchor="middle" font-size="28" font-family="sans-serif" letter-spacing="6" fill="{fg}">{source_label}</text>
{title_nodes}
<line x1="350" y1="1200" x2="850" y2="1200" stroke="{accent}" stroke-width="5"/>
<text x="600" y="1300" text-anchor="middle" font-size="42" font-family="sans-serif" fill="{fg}">{esc(row['author'])}</text>
<text x="600" y="1460" text-anchor="middle" font-size="22" font-family="sans-serif" fill="{fg}" opacity=".75">DONNÉE FICTIVE — CIFERECA</text>
</svg>'''

    @staticmethod
    def chapter_html(row: sqlite3.Row) -> str:
        rng = random.Random(stable_int(row["source"], row["book_id"], row["title"]))
        weather = rng.choice([
            "une pluie fine rayait les vitres", "le brouillard descendait sur les toits",
            "un vent salé remontait les rues", "le soleil pâle allongeait les ombres",
        ])
        sound = rng.choice([
            "le grincement d'une enseigne", "le souffle régulier des vagues",
            "le claquement lointain d'une porte", "le sifflement d'un train",
        ])
        discovery = rng.choice([
            "un nom qu'elle n'avait jamais vu", "une date impossible",
            "une phrase écrite de sa propre main", "un plan marqué d'une croix",
        ])
        p = [
            f"À {row['place']}, {weather}. Depuis l'aube, personne ne parlait de ce qui s'était passé la veille.",
            f"Camille était venue pour comprendre pourquoi le {row['object_name']} apparaissait dans les souvenirs de sa famille depuis trois générations.",
            f"Elle ouvrit l'enveloppe posée sur la table. À l'intérieur, elle découvrit {discovery}. Dehors, {sound} sembla soudain beaucoup plus proche.",
            f"Au dos de la feuille, quelques mots reprenaient exactement le titre : « {row['title']} ». Quelqu'un savait qu'elle viendrait.",
            "Elle glissa la feuille dans sa poche et sortit. Le premier secret venait de s'ouvrir, et la journée ne suivrait aucun des plans qu'elle avait faits.",
            "Ce chapitre est un texte fictif généré automatiquement pour les tests fonctionnels de Cifereca.",
        ]
        return "\n".join(f"<p>{esc(x)}</p>" for x in p)

    @staticmethod
    def _book_from_row(row: sqlite3.Row) -> FakeBook:
        return FakeBook(
            index=int(row["book_id"]),
            identifier=f"urn:cifereca:fake:{row['source']}:{row['book_id']}",
            title=str(row["title"]),
            author=str(row["author"]),
            publisher=str(row["publisher"]),
            publication_date=str(row["published"]),
            subject=str(row["subject"]),
            language=str(row["language"]),
            place=str(row["place"]),
            object_name=str(row["object_name"]),
            description=str(row["summary"]),
        )

    def write_epub(self, path: Path, row: sqlite3.Row):
        write_shared_epub(path, self._book_from_row(row))

    def not_found(self, handler):
        self.send_json(handler, HTTPStatus.NOT_FOUND, {"error": "not found"})

    def route(self, handler: BaseHTTPRequestHandler):
        parsed = urlsplit(handler.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query, keep_blank_values=True)

        if path == "/":
            return self.home(handler)
        if path == "/health":
            return self.health(handler)
        if path == "/gallica/opds":
            return self.gallica_root(handler)
        if path == "/gallica/opensearch.xml":
            return self.gallica_opensearch(handler)
        if path == "/gallica/services/engine/search/opds":
            return self.gallica_search(handler, qs)
        if path == "/gutenberg/ebooks/search.opds":
            return self.gutenberg_search(handler, qs)

        m = re.fullmatch(r"/(gallica|gutenberg)/covers/(\d+)\.png", path)
        if m:
            return self.cover(handler, m.group(1), int(m.group(2)))
        m = re.fullmatch(r"/(gallica|gutenberg)/epub/(\d+)\.epub", path)
        if m:
            return self.epub(handler, m.group(1), int(m.group(2)))
        m = re.fullmatch(r"/gallica/pdf/(\d+)\.pdf", path)
        if m:
            return self.pdf(handler, int(m.group(1)))
        m = re.fullmatch(r"/(gallica|gutenberg)/books/(\d+)", path)
        if m:
            return self.book_page(handler, m.group(1), int(m.group(2)))
        return self.not_found(handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "CiferecaFakeOPDS/1.0"
    app: FakeOPDSApp

    def do_GET(self):
        try:
            self.app.route(self)
        except (ValueError, OverflowError) as exc:
            self.app.send_json(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except BrokenPipeError:
            pass
        except Exception as exc:
            print(f"[error] {self.path}: {exc}", file=sys.stderr)
            try:
                self.app.send_json(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": type(exc).__name__, "detail": str(exc)})
            except BrokenPipeError:
                pass

    def log_message(self, fmt: str, *args):
        sys.stderr.write("[http] %s - %s\n" % (self.address_string(), fmt % args))


def parse_args():
    p = argparse.ArgumentParser(description="Pseudo serveur HTTP OPDS pour tests Cifereca")
    p.add_argument("--bind", default="127.0.0.1", help="Adresse d'écoute (défaut: 127.0.0.1)")
    p.add_argument("--port", type=int, default=8080, help="Port HTTP (défaut: 8080)")
    p.add_argument("--cache-dir", type=Path, default=Path(".opds-fake-cache"), help="Répertoire du cache")
    p.add_argument("--books-per-source", type=int, default=DEFAULT_BOOKS_PER_SOURCE, help="Nombre de notices par source (défaut: 10000)")
    p.add_argument("--seed", type=int, default=20261006, help="Graine déterministe")
    p.add_argument("--broken-assets-every", type=int, default=0, help="Simule une panne cover/EPUB tous les N livres (0=désactivé)")
    p.add_argument("--rebuild", action="store_true", help="Reconstruire entièrement le cache")
    p.add_argument("--build-cache-only", action="store_true", help="Générer/valider le cache puis quitter")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.books_per_source < 1:
        raise SystemExit("--books-per-source doit être >= 1")
    if not (1 <= args.port <= 65535):
        raise SystemExit("--port doit être compris entre 1 et 65535")
    if args.broken_assets_every < 0:
        raise SystemExit("--broken-assets-every doit être >= 0")

    cache = DatasetCache(args.cache_dir, args.books_per_source, args.seed, args.rebuild)
    if args.build_cache_only:
        print(json.dumps({source: cache.count(source) for source in SOURCES}, ensure_ascii=False))
        return 0

    app = FakeOPDSApp(cache, broken_assets_every=args.broken_assets_every)
    Handler.app = app
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"[server] http://{args.bind}:{args.port}/")
    print(f"[server] Gallica    : http://{args.bind}:{args.port}/gallica/opds")
    print(f"[server] Gutenberg : http://{args.bind}:{args.port}/gutenberg/ebooks/search.opds/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] arrêt")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())