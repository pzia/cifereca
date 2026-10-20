#!/usr/bin/env python3
"""
pseudo_kobo_services.py — faux services HTTP Kobo Catalogue + Kobo Vault pour Cifereca.

JDD par défaut :
  - catalogue Kobo : 10 000 livres fictifs
  - Kobo Vault     : 1 000 livres possédés, sous-ensemble du catalogue

Le catalogue et la bibliothèque sont générés au démarrage et conservés dans un
cache SQLite persistant. Les couvertures et EPUB de test sont générés à la
demande puis mis en cache sur disque.

Le même exécutable peut exposer :
  --service catalog   : pseudo catalogue uniquement
  --service vault     : pseudo Vault (inclut /catalog/search pour compatibilité
                        avec le KoboVaultClient actuel de Cifereca)
  --service combined  : ensemble des routes (défaut)

Exemples :
  python3 pseudo_kobo_services.py
  python3 pseudo_kobo_services.py --service catalog --port 8091
  python3 pseudo_kobo_services.py --service vault --port 8092
  python3 pseudo_kobo_services.py --rebuild --build-cache-only

Configuration Cifereca (mode vault/combined) :
  base_url  = http://127.0.0.1:8090
  api_token = kvt_test

Routes principales :
  GET  /api/v2/status
  GET  /api/v2/account
  GET  /api/v2/catalog/search?q=...&field=all&limit=50&page=0
  GET  /api/v2/library/search?q=...&field=all&limit=50&cursor=...
  GET  /api/v2/library/manifest?limit=200&cursor=...
  GET  /api/v2/library/{product_id}
  POST /api/v2/library/{product_id}/acquisition

Dépendances : Python 3, bibliothèque standard uniquement.
"""

from __future__ import annotations

import argparse
import base64
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
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

DATASET_ROOT = Path(__file__).resolve().parents[1]
if str(DATASET_ROOT) not in sys.path:
    sys.path.insert(0, str(DATASET_ROOT))

from common.fake_books import FakeBook, FakeBookFactory, cover_png_bytes, write_epub as write_shared_epub

APP_VERSION = "1.1"
CACHE_SCHEMA_VERSION = "1"
DEFAULT_CATALOG_BOOKS = 10_000
DEFAULT_VAULT_BOOKS = 1_000
DEFAULT_TOKEN = "kvt_test"
MAX_SEARCH_LIMIT = 100
MAX_MANIFEST_LIMIT = 500

FIRST_NAMES = [
    "Adèle", "Alice", "Bastien", "Camille", "Diane", "Élias", "Émile",
    "Inès", "Jules", "Lina", "Maël", "Nora", "Oscar", "Salomé", "Théo",
    "Victor", "Yasmine", "Zoé", "Lucien", "Madeleine", "Auguste", "Céleste",
    "Anaïs", "Noé", "Léonie", "Martin", "Iris", "Gabriel", "Louise", "Simon",
]
LAST_NAMES = [
    "Armand", "Beaulieu", "Caron", "Delmas", "Faure", "Garnier", "Hébert",
    "Lefèvre", "Moreau", "Perrin", "Roche", "Valette", "Vidal", "Bernier",
    "Clément", "Dumont", "Fournier", "Girard", "Laurent", "Marchand",
    "Renaud", "Boulanger", "Lenoir", "Aubert", "Deschamps", "Renard",
]
PUBLISHERS = [
    "Éditions du Passage", "Maison Sillage", "Atelier Boréal", "La Lucarne",
    "Les Presses du Rivage", "Éditions des Heures", "Le Papier Bleu",
    "Atelier des Lettres", "Maison des Brumes", "Éditions du Méridien",
    "Presses du Septentrion", "Les Livres du Phare", "Studio Papier",
]
PLACES = [
    "la baie des Brumes", "le quartier des Verrières", "la vallée d'Argile",
    "l'île aux Lanternes", "la gare de Minuit", "les collines de Verre",
    "le port des Écluses", "la forêt de Cendre", "la ville des Horloges",
    "le jardin des Amandiers", "le quai des Ombres", "la maison du Phare",
    "la rue des Cartographes", "le passage des Étoiles", "la plaine d'Azur",
    "le village des Saules", "la tour des Vents", "le lac des Silences",
    "les falaises d'Opale", "le canal des Songes", "la place des Tisserands",
    "le domaine des Roches", "la côte des Alizés", "le val des Lucioles",
]
OBJECTS = [
    "phare", "carnet", "miroir", "violon", "passage", "jardin", "train",
    "secret", "studio", "silence", "sablier", "coffret", "compas", "portrait",
    "manuscrit", "fanion", "atlas", "bagage", "médaillon", "livre", "sextant",
    "piano", "navire", "belvédère", "cabinet", "grenier", "pont", "théâtre",
]
QUALIFIERS = [
    "oublié", "silencieux", "invisible", "bleu", "d'hiver", "de minuit",
    "des brumes", "des jours perdus", "aux reflets d'or", "du dernier été",
    "de verre", "des marées", "des voyageurs", "des secrets", "du nord",
    "des absents", "sans retour", "aux mille fenêtres", "sous la pluie",
    "des heures lentes", "de l'autre rive", "du bout du monde",
]
MOMENTS = [
    "Avant l'aube", "Après minuit", "Au dernier hiver", "Un soir de septembre",
    "À l'heure des marées", "Au printemps des absents", "Quand revient le vent",
    "Le jour des lucioles", "À la fin de l'été", "Sous la première neige",
]
MOTIFS = [
    "des ombres", "des étoiles", "des marées", "des oiseaux", "des cendres",
    "des lucioles", "des cartes", "des lettres", "des voix", "des saisons",
    "des chemins", "des fenêtres", "des îles", "des souvenirs", "des voyageurs",
]
CHARACTERS = [
    "La cartographe", "Le veilleur", "La relieuse", "Le marin", "L'horloger",
    "La violoniste", "Le libraire", "L'arpenteuse", "Le photographe",
    "La gardienne", "Le passeur", "La botaniste", "Le facteur", "L'astronome",
]
SUBJECTS = [
    "Roman", "Mystère", "Aventure", "Fantastique", "Histoire", "Voyage",
    "Poésie", "Science-fiction", "Fiction contemporaine", "Enquête", "Nature",
    "Arts", "Musique", "Mémoire", "Société", "Correspondance", "Exploration",
]
LANGUAGES = ["fr", "fr", "fr", "fr", "fr", "en", "de", "es", "it"]
TITLE_PATTERNS = [
    "Le {object} — {place}", "Le {object} {qualifier}",
    "Le {object} {qualifier} — {place}", "La mémoire — {place}",
    "La mémoire {qualifier}", "Les nuits — {place}", "Les nuits {qualifier}",
    "Sous le ciel, {place}", "Sous le ciel {qualifier}", "Le secret — {place}",
    "Le secret du {object}", "Une saison — {place}", "Une saison {qualifier}",
    "Chroniques — {place}", "Chroniques du {object} {qualifier}",
    "L'ombre du {object}", "L'ombre sur {place}", "Le dernier {object}",
    "Le dernier {object} — {place}", "{moment} — {place}", "{moment}, le {object}",
    "La route {motif}", "Le livre {motif}", "{character} — {place}",
    "{character} et le {object} {qualifier}", "Ce que racontent {motif}",
    "Là où dorment {motif}", "Quand disparaît le {object}",
    "Quand revient {character_lower}", "Carnet de voyage : {place}",
]
PALETTES = [
    ("#183153", "#f4d35e", "#ffffff"), ("#3b1f47", "#e07a5f", "#f4f1de"),
    ("#143642", "#ec9a29", "#ffffff"), ("#4f2d3f", "#d9b44a", "#f7f3e8"),
    ("#1d3557", "#e63946", "#f1faee"), ("#264653", "#e9c46a", "#f4f1de"),
    ("#293241", "#ee6c4d", "#e0fbfc"), ("#2b2d42", "#ef233c", "#edf2f4"),
]


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_int(*parts: object) -> int:
    digest = hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower() or "book"


def make_unique_title(rng: random.Random, used: set[str], index: int) -> tuple[str, str, str]:
    for _ in range(200):
        place = rng.choice(PLACES)
        obj = rng.choice(OBJECTS)
        character = rng.choice(CHARACTERS)
        title = rng.choice(TITLE_PATTERNS).format(
            object=obj,
            place=place,
            qualifier=rng.choice(QUALIFIERS),
            moment=rng.choice(MOMENTS),
            motif=rng.choice(MOTIFS),
            character=character,
            character_lower=character[0].lower() + character[1:],
        )
        if title not in used:
            used.add(title)
            return title, place, obj
    title = f"Carnet Kobo fictif — {index:05d}"
    used.add(title)
    return title, rng.choice(PLACES), rng.choice(OBJECTS)


def make_summary(title: str, place: str, subject: str) -> str:
    return (
        f"Livre fictif de test Kobo. « {title} » se déroule autour de {place} et "
        f"mêle {subject.lower()}, découverte et mémoire. Cette notice est générée "
        "uniquement pour les tests fonctionnels de Cifereca."
    )


def encode_cursor(offset: int) -> str:
    raw = f"offset:{max(0, offset)}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value: str | None) -> int:
    if not value:
        return 0
    try:
        raw = value + "=" * (-len(value) % 4)
        text = base64.urlsafe_b64decode(raw.encode("ascii")).decode("ascii")
        if not text.startswith("offset:"):
            raise ValueError
        return max(0, int(text.split(":", 1)[1]))
    except Exception as exc:
        raise ValueError("cursor invalide") from exc


class DatasetCache:
    def __init__(
        self,
        cache_dir: Path,
        catalog_books: int,
        vault_books: int,
        seed: int,
        rebuild: bool = False,
    ) -> None:
        if vault_books > catalog_books:
            raise ValueError("--vault-books ne peut pas dépasser --catalog-books")
        self.cache_dir = cache_dir
        self.catalog_books = catalog_books
        self.vault_books = vault_books
        self.seed = seed
        self.db_path = cache_dir / "kobo.sqlite3"
        self.cover_dir = cache_dir / "covers"
        self.epub_dir = cache_dir / "epub"
        self.acsm_dir = cache_dir / "acsm"
        self._local = threading.local()
        if rebuild and cache_dir.exists():
            shutil.rmtree(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.cover_dir.mkdir(parents=True, exist_ok=True)
        self.epub_dir.mkdir(parents=True, exist_ok=True)
        self.acsm_dir.mkdir(parents=True, exist_ok=True)
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
                book_id INTEGER PRIMARY KEY,
                product_id TEXT UNIQUE NOT NULL,
                revision_id TEXT NOT NULL,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                publisher TEXT NOT NULL,
                publication_date TEXT NOT NULL,
                language TEXT NOT NULL,
                subject TEXT NOT NULL,
                description TEXT NOT NULL,
                isbn TEXT NOT NULL,
                price_amount TEXT NOT NULL,
                currency TEXT NOT NULL,
                format TEXT NOT NULL,
                drm_type TEXT NOT NULL,
                place TEXT NOT NULL,
                object_name TEXT NOT NULL,
                cover_variant INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ownership (
                product_id TEXT PRIMARY KEY,
                owned_order INTEGER UNIQUE NOT NULL,
                archived INTEGER NOT NULL,
                downloadable INTEGER NOT NULL,
                owned_at TEXT NOT NULL,
                FOREIGN KEY(product_id) REFERENCES books(product_id)
            );
            CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);
            CREATE INDEX IF NOT EXISTS idx_books_author ON books(author);
            CREATE INDEX IF NOT EXISTS idx_books_subject ON books(subject);
            CREATE INDEX IF NOT EXISTS idx_books_isbn ON books(isbn);
            CREATE INDEX IF NOT EXISTS idx_ownership_order ON ownership(owned_order);
            """
        )
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        catalog_count = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        vault_count = conn.execute("SELECT COUNT(*) FROM ownership").fetchone()[0]
        valid = (
            meta.get("schema_version") == CACHE_SCHEMA_VERSION
            and meta.get("app_version") == APP_VERSION
            and meta.get("catalog_books") == str(self.catalog_books)
            and meta.get("vault_books") == str(self.vault_books)
            and meta.get("seed") == str(self.seed)
            and catalog_count == self.catalog_books
            and vault_count == self.vault_books
        )
        if valid:
            print(f"[cache] Réutilisation {self.db_path} — catalogue={catalog_count}, vault={vault_count}")
            conn.close()
            return

        print(f"[cache] Génération catalogue Kobo : {self.catalog_books} notices…")
        conn.execute("DELETE FROM ownership")
        conn.execute("DELETE FROM books")
        conn.execute("DELETE FROM meta")
        rng = random.Random(stable_int(self.seed, "kobo-service"))
        factory = FakeBookFactory(self.seed, "kobo")
        first_date = date(1998, 1, 1)
        span_days = (date(2026, 9, 30) - first_date).days
        batch = []
        for book_id in range(1, self.catalog_books + 1):
            book = factory.make(book_id)
            publication_date = first_date + timedelta(days=rng.randrange(span_days + 1))
            product_id = f"kobo-fake-{book_id:05d}"
            revision_id = f"rev-{stable_int(self.seed, product_id) % 10**12:012d}"
            isbn = f"978{stable_int(self.seed, 'isbn', book_id) % 10_000_000_000:010d}"
            price = f"{rng.choice([299, 399, 499, 599, 699, 799, 899, 999, 1199, 1299]) / 100:.2f}"
            drm_roll = stable_int(self.seed, "drm", book_id) % 100
            if drm_roll < 78:
                drm_type, fmt = "SignedNoDrm", "EPUB3"
            elif drm_roll < 93:
                drm_type, fmt = "KDRM", "KEPUB"
            else:
                drm_type, fmt = "AdobeDrm", "EPUB"
            batch.append((
                book_id, product_id, revision_id, book.title, book.author, book.publisher,
                publication_date.isoformat(), book.language, book.subject,
                book.description, isbn, price, "EUR", fmt,
                drm_type, book.place, book.object_name, rng.randrange(len(PALETTES)),
            ))
            if len(batch) >= 1000:
                conn.executemany("INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                batch.clear()
        if batch:
            conn.executemany("INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
        conn.commit()

        print(f"[cache] Génération Kobo Vault : {self.vault_books} livres possédés…")
        own_rng = random.Random(stable_int(self.seed, "kobo-vault"))
        selected = own_rng.sample(range(1, self.catalog_books + 1), self.vault_books)
        owned_rows = []
        owned_base = date(2020, 1, 1)
        for order, book_id in enumerate(selected, 1):
            product_id = f"kobo-fake-{book_id:05d}"
            archived = 1 if stable_int(self.seed, "archived", book_id) % 100 < 4 else 0
            downloadable = 0 if archived else 1
            owned_at = owned_base + timedelta(days=stable_int(self.seed, "owned", book_id) % 2400)
            owned_rows.append((product_id, order, archived, downloadable, owned_at.isoformat()))
        conn.executemany("INSERT INTO ownership VALUES (?,?,?,?,?)", owned_rows)
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("schema_version", CACHE_SCHEMA_VERSION),
                ("app_version", APP_VERSION),
                ("catalog_books", str(self.catalog_books)),
                ("vault_books", str(self.vault_books)),
                ("seed", str(self.seed)),
                ("generated_at", iso_now()),
            ],
        )
        conn.commit()
        conn.close()
        print(f"[cache] Prêt : {self.db_path}")

    @staticmethod
    def _column(field: str) -> str:
        return {
            "title": "b.title",
            "author": "b.author",
            "subject": "b.subject",
            "isbn": "b.isbn",
            "language": "b.language",
        }.get(str(field or "").casefold(), "")

    def counts(self) -> dict[str, int]:
        conn = self.connect()
        return {
            "catalog": conn.execute("SELECT COUNT(*) FROM books").fetchone()[0],            "vault": conn.execute("SELECT COUNT(*) FROM ownership").fetchone()[0],
        }

    def get(self, product_id: str) -> sqlite3.Row | None:
        return self.connect().execute(
            """SELECT b.*, o.owned_order, o.archived, o.downloadable, o.owned_at
               FROM books b LEFT JOIN ownership o ON o.product_id=b.product_id
               WHERE b.product_id=?""",
            (product_id,),
        ).fetchone()

    def search_catalog(self, query: str, field: str, offset: int, limit: int, language: str = ""):
        conn = self.connect()
        query = str(query or "").strip()
        field_col = self._column(field)
        where = []
        params: list[object] = []
        terms = [x for x in re.split(r"\s+", query) if x]
        expr = field_col or "lower(b.title || ' ' || b.author || ' ' || b.subject || ' ' || b.publisher || ' ' || b.isbn)"
        if field_col:
            expr = f"lower({field_col})"
        for term in terms:
            where.append(expr + " LIKE ?")
            params.append("%" + term.casefold() + "%")
        language = str(language or "").strip().casefold()
        if language:
            where.append("lower(b.language) = ?")
            params.append(language)
        sql_where = ("WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(f"SELECT COUNT(*) FROM books b {sql_where}", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT b.*, o.owned_order, o.archived, o.downloadable, o.owned_at
                FROM books b LEFT JOIN ownership o ON o.product_id=b.product_id
                {sql_where} ORDER BY b.book_id LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        return total, rows

    def search_library(self, query: str, field: str, offset: int, limit: int):
        conn = self.connect()
        query = str(query or "").strip()
        field_col = self._column(field)
        where = []
        params: list[object] = []
        terms = [x for x in re.split(r"\s+", query) if x]
        expr = field_col or "lower(b.title || ' ' || b.author || ' ' || b.subject || ' ' || b.publisher || ' ' || b.isbn)"
        if field_col:
            expr = f"lower({field_col})"
        for term in terms:
            where.append(expr + " LIKE ?")
            params.append("%" + term.casefold() + "%")
        suffix = (" AND " + " AND ".join(where)) if where else ""
        total = conn.execute(
            f"SELECT COUNT(*) FROM ownership o JOIN books b ON b.product_id=o.product_id WHERE 1=1{suffix}",
            params,
        ).fetchone()[0]
        rows = conn.execute(
            f"""SELECT b.*, o.owned_order, o.archived, o.downloadable, o.owned_at
                FROM ownership o JOIN books b ON b.product_id=o.product_id
                WHERE 1=1{suffix} ORDER BY o.owned_order LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        return total, rows

    def manifest(self, offset: int, limit: int):
        conn = self.connect()
        total = conn.execute("SELECT COUNT(*) FROM ownership").fetchone()[0]
        rows = conn.execute(
            """SELECT b.*, o.owned_order, o.archived, o.downloadable, o.owned_at
               FROM ownership o JOIN books b ON b.product_id=o.product_id
               ORDER BY o.owned_order LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return total, rows

    def cover_path(self, product_id: str) -> Path:
        return self.cover_dir / f"{product_id}.png"

    def epub_path(self, product_id: str) -> Path:
        return self.epub_dir / f"{product_id}.epub"

    def acsm_path(self, product_id: str) -> Path:
        return self.acsm_dir / f"{product_id}.acsm"


class KoboFakeApp:
    def __init__(
        self,
        cache: DatasetCache,
        *,
        service: str,
        token: str,
        accept_any_token: bool,
        broken_assets_every: int = 0,
    ) -> None:
        self.cache = cache
        self.service = service
        self.token = token
        self.accept_any_token = accept_any_token
        self.broken_assets_every = max(0, int(broken_assets_every))

    def asset_is_broken(self, row: sqlite3.Row) -> bool:
        return self.broken_assets_every > 0 and int(row["book_id"]) % self.broken_assets_every == 0

    @staticmethod
    def base(handler: BaseHTTPRequestHandler) -> str:
        host = handler.headers.get("Host") or f"127.0.0.1:{handler.server.server_port}"
        proto = handler.headers.get("X-Forwarded-Proto", "http").split(",", 1)[0].strip() or "http"
        return f"{proto}://{host}"

    @staticmethod
    def send_bytes(handler, status: int, content_type: str, body: bytes, *, extra_headers=None, cache_control="no-cache"):
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", cache_control)
        for key, value in (extra_headers or {}).items():
            handler.send_header(key, str(value))
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)

    def send_json(self, handler, status: int, obj: object, *, extra_headers=None):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_bytes(handler, status, "application/json; charset=utf-8", body, extra_headers=extra_headers)

    def error(self, handler, status: int, code: str, message: str):
        self.send_json(handler, status, {"error": {"code": code, "message": message}})

    def authenticated(self, handler) -> bool:
        value = str(handler.headers.get("Authorization") or "")
        if not value.startswith("Bearer "):
            self.error(handler, HTTPStatus.UNAUTHORIZED, "missing_token", "Bearer token requis")
            return False
        supplied = value[7:].strip()
        if not supplied or (not self.accept_any_token and supplied != self.token):
            self.error(handler, HTTPStatus.UNAUTHORIZED, "invalid_token", "token API refusé")
            return False
        return True

    def allows_catalog(self) -> bool:
        return self.service in {"catalog", "vault", "combined"}

    def allows_vault(self) -> bool:
        return self.service in {"vault", "combined"}

    def item_json(self, handler, row: sqlite3.Row, *, library: bool = False) -> dict[str, object]:
        base = self.base(handler)
        owned = row["owned_order"] is not None
        archived = bool(row["archived"]) if owned else False
        downloadable = bool(row["downloadable"]) if owned else False
        return {
            "product_id": row["product_id"],
            "revision_id": row["revision_id"] if owned else None,
            "title": row["title"],
            "authors": [row["author"]],
            "publisher": row["publisher"],
            "publication_date": row["publication_date"],
            "language": row["language"],
            "languages": [row["language"]],
            "subject": row["subject"],
            "description": f"<p>{esc(row['description'])}</p>",
            "isbn": row["isbn"],
            "format": row["format"],
            "content_type": "ebook",
            "price": {"amount": row["price_amount"], "currency": row["currency"]},
            "currency": row["currency"],
            "cover": {
                "thumbnail": f"{base}/assets/covers/{quote(row['product_id'])}.png",
                "large": f"{base}/assets/covers/{quote(row['product_id'])}.png",
            },
            "product_url": f"{base}/store/books/{quote(row['product_id'])}",
            "owned": owned,
            "downloadable": downloadable,
            "archived": archived,
            "collection": "library" if owned else "catalog",
            "owned_at": row["owned_at"] if owned else None,
            "drm_type": row["drm_type"] if library or owned else None,
        }

    def root(self, handler):
        base = self.base(handler)
        self.send_json(handler, HTTPStatus.OK, {
            "name": "Cifereca fake Kobo services",
            "version": APP_VERSION,
            "mode": self.service,
            "catalog_books": self.cache.catalog_books,
            "vault_books": self.cache.vault_books,
            "token": "<configured bearer token>",
            "routes": {
                "status": base + "/api/v2/status",
                "catalog_search": base + "/api/v2/catalog/search",
                "library_manifest": base + "/api/v2/library/manifest",
                "health": base + "/health",
            },
            "faults": {"broken_assets_every": self.broken_assets_every},
        })

    def health(self, handler):
        self.send_json(handler, HTTPStatus.OK, {
            "ok": True,
            "version": APP_VERSION,
            "service": self.service,
            "counts": self.cache.counts(),
        })

    def status(self, handler):
        if not self.allows_vault():
            return self.error(handler, HTTPStatus.NOT_FOUND, "not_found", "Vault désactivé")
        self.send_json(handler, HTTPStatus.OK, {
            "ok": True,
            "api_version": "2.3",
            "version": "2.3",
            "release": "fake-1.0",
            "service": "kobo-vault",
            "role": "kobo-library-provider",
            "authentication": {
                "type": "bearer",
                "token_prefix": "kvt_",
                "required_for_private_capabilities": True,
            },
            "capabilities": ["catalog_search", "library_search", "library", "acquisition"],
            "capability_details": [
                {"id": "catalog_search", "available": True},
                {"id": "library_search", "available": True},
                {"id": "library", "available": True, "count": self.cache.vault_books},
                {"id": "acquisition", "available": True, "schema_version": "1.4"},
            ],
            "acquisition_access": "configured",
        })

    def account(self, handler):
        if not self.allows_vault():
            return self.error(handler, HTTPStatus.NOT_FOUND, "not_found", "Vault désactivé")
        self.send_json(handler, HTTPStatus.OK, {
            "account": {
                "id": "acct-kobo-fake-001",
                "display_name": f"Kobo Test — {self.cache.vault_books:,} livres".replace(",", " "),
                "email_hint": "fake-kobo@example.invalid",
            }
        })

    def catalog_search(self, handler, qs):
        if not self.allows_catalog():
            return self.error(handler, HTTPStatus.NOT_FOUND, "not_found", "Catalogue désactivé")
        # #771: the already-supported catalogue endpoint can explicitly replay
        # the same HTTP status failures as the private manifest. Only these
        # stateless failures are shared: cursor_loop remains manifest-only.
        if (qs.get("ux_fault") or [""])[0] in {
            "429", "503", "expired", "malformed_json",
        }:
            return self.manifest_fault(handler, qs)
        query = (qs.get("q") or [""])[0]
        field = (qs.get("field") or ["all"])[0]
        limit = max(1, min(MAX_SEARCH_LIMIT, int((qs.get("limit") or ["50"])[0] or 50)))
        page = max(0, int((qs.get("page") or ["0"])[0] or 0))
        language = (qs.get("language") or [""])[0].strip().casefold()
        total, rows = self.cache.search_catalog(query, field, page * limit, limit, language)
        self.send_json(handler, HTTPStatus.OK, {
            "items": [self.item_json(handler, r) for r in rows],
            "page": page,
            "limit": limit,
            "total": total,
        })

    def library_search(self, handler, qs):
        if not self.allows_vault():
            return self.error(handler, HTTPStatus.NOT_FOUND, "not_found", "Vault désactivé")
        query = (qs.get("q") or [""])[0]
        field = (qs.get("field") or ["all"])[0]
        limit = max(1, min(MAX_SEARCH_LIMIT, int((qs.get("limit") or ["50"])[0] or 50)))
        offset = decode_cursor((qs.get("cursor") or [None])[0])
        total, rows = self.cache.search_library(query, field, offset, limit)
        next_cursor = encode_cursor(offset + limit) if offset + limit < total else None
        self.send_json(handler, HTTPStatus.OK, {
            "items": [self.item_json(handler, r, library=True) for r in rows],
            "next_cursor": next_cursor,
            "total": total,
        })

    def manifest_fault(self, handler, qs) -> bool:
        """Opt-in negative Vault fixtures; ordinary requests are unchanged.

        These endpoints are synthetic and HTTP-local. A fault must be explicitly
        requested through ux_fault; no stored token, provider or book is touched.
        """
        fault = (qs.get("ux_fault") or [""])[0]
        if fault == "429":
            self.send_json(
                handler, HTTPStatus.TOO_MANY_REQUESTS,
                {"error": {"code": "rate_limited", "message": "Réessayer plus tard"}},
                extra_headers={"Retry-After": "2"},
            )
        elif fault == "503":
            self.error(handler, HTTPStatus.SERVICE_UNAVAILABLE,
                       "temporary_unavailable", "Service fictif indisponible")
        elif fault == "expired":
            self.error(handler, HTTPStatus.UNAUTHORIZED,
                       "expired_token", "Session fictive expirée")
        elif fault == "malformed_json":
            self.send_bytes(handler, HTTPStatus.OK,
                            "application/json; charset=utf-8", b'{"items":[')
        elif fault == "cursor_loop":
            offset = decode_cursor((qs.get("cursor") or [None])[0])
            total, rows = self.cache.manifest(offset, 2)
            self.send_json(handler, HTTPStatus.OK, {
                "items": [self.item_json(handler, row, library=True) for row in rows],
                "next_cursor": encode_cursor(offset),
                "total": total,
            })
        else:
            return False
        return True

    def manifest(self, handler, qs):
        if not self.allows_vault():
            return self.error(handler, HTTPStatus.NOT_FOUND, "not_found", "Vault désactivé")
        limit = max(1, min(MAX_MANIFEST_LIMIT, int((qs.get("limit") or ["200"])[0] or 200)))
        offset = decode_cursor((qs.get("cursor") or [None])[0])
        total, rows = self.cache.manifest(offset, limit)
        next_cursor = encode_cursor(offset + limit) if offset + limit < total else None
        self.send_json(handler, HTTPStatus.OK, {
            "items": [self.item_json(handler, r, library=True) for r in rows],
            "next_cursor": next_cursor,
            "total": total,
        })

    def library_item(self, handler, product_id: str):
        if not self.allows_vault():
            return self.error(handler, HTTPStatus.NOT_FOUND, "not_found", "Vault désactivé")
        row = self.cache.get(product_id)
        if row is None or row["owned_order"] is None:
            return self.error(handler, HTTPStatus.NOT_FOUND, "not_found", "livre absent du Vault")
        self.send_json(handler, HTTPStatus.OK, {"item": self.item_json(handler, row, library=True)})

    def acquisition(self, handler, product_id: str, body: dict[str, object]):
        if not self.allows_vault():
            return self.error(handler, HTTPStatus.NOT_FOUND, "not_found", "Vault désactivé")
        row = self.cache.get(product_id)
        if row is None or row["owned_order"] is None:
            return self.error(handler, HTTPStatus.NOT_FOUND, "not_found", "livre absent du Vault")
        if bool(row["archived"]):
            return self.error(handler, HTTPStatus.CONFLICT, "archived_book", "livre archivé")

        mode = str(body.get("mode") or "descriptor").casefold()
        if mode not in {"descriptor", "delegated"}:
            return self.error(handler, HTTPStatus.BAD_REQUEST, "invalid_mode", "mode invalide")
        revision_id = str(body.get("revision_id") or row["revision_id"])
        profile = str(body.get("display_profile") or "Android")
        if profile not in {"Android", "Desktop"}:
            return self.error(handler, HTTPStatus.BAD_REQUEST, "invalid_display_profile", "DisplayProfile invalide")
        include_acsm = bool(body.get("include_acsm", False))
        base = self.base(handler)
        drm_type = row["drm_type"]
        now = iso_now()
        candidate: dict[str, object]
        mechanisms: list[str]
        sources: dict[str, object]

        if drm_type == "SignedNoDrm":
            candidate = {
                "kind": "http", "transport": "http", "method": "GET",
                "url": f"{base}/assets/epub/{quote(product_id)}.epub", "headers": {},
                "media_format": "epub", "source_format": "EPUB3", "handling": "http_plain",
                "drm": {"family": "none", "raw_type": "SignedNoDrm", "protected": False},
                "kobo": {"source": "content_access_book", "display_profile": profile,
                         "content_keys_present": False, "content_key_count": 0,
                         "requires_kobo_authorization": False},
                "drm_type": "SignedNoDrm", "format": "epub3", "protected": False,
            }
            mechanisms = ["http_plain"]
            sources = {"content_access_book": {"status": "available", "candidate_count": 1}}
        elif drm_type == "AdobeDrm" and include_acsm:
            candidate = {
                "kind": "acsm", "transport": "http", "method": "GET",
                "url": f"{base}/assets/acsm/{quote(product_id)}.acsm", "headers": {},
                "media_format": "acsm", "source_format": "ACSM", "handling": "adobe_adept",
                "drm": {"family": "adobe_adept", "raw_type": "AdobeDrm", "protected": True},
                "kobo": {"source": "library_download_link", "display_profile": None,
                         "content_keys_present": False, "content_key_count": 0,
                         "requires_kobo_authorization": False},
                "drm_type": "AdobeDrm", "format": "acsm", "protected": True,
            }
            mechanisms = ["adobe_adept"]
            sources = {"acsm_export": {"requested": True, "status": "available", "candidate_count": 1}}
        else:
            candidate = {
                "kind": "http", "transport": "http", "method": "GET",
                "url": f"{base}/assets/kepub/{quote(product_id)}.kepub", "headers": {},
                "media_format": "kepub", "source_format": "KEPUB", "handling": "kobo_kdrm",
                "drm": {"family": "kobo_kdrm", "raw_type": "KDRM", "protected": True},
                "kobo": {"source": "content_access_book", "display_profile": profile,
                         "content_keys_present": True, "content_key_count": 12,
                         "requires_kobo_authorization": False},
                "drm_type": "KDRM", "format": "kepub", "protected": True,
            }
            mechanisms = ["kobo_kdrm"]
            sources = {"content_access_book": {"status": "available", "candidate_count": 1}}

        acquisition = {
            "product_id": product_id,
            "revision_id": revision_id,
            "mode": mode,
            "display_profile": profile,
            "mechanisms": mechanisms,
            "sources": sources,
            "content_keys": {
                "present": drm_type == "KDRM", "count": 12 if drm_type == "KDRM" else 0,
                "values_exposed": False,
                "retrieval": "consumer_replays_content_access" if mode == "delegated" and drm_type == "KDRM" else "not_exposed",
            },
            "content_keys_exposed": False,
            "credentials_exposed": mode == "delegated",
            "validity": {"generated_at": now, "expires_at": None, "renewable": True, "refresh_strategy": "resolve_again"},
            "candidates": [candidate],
        }
        if mode == "delegated":
            acquisition["delegation"] = {
                "provider": "kobo",
                "sensitive": True,
                "content_access_request": {
                    "url": f"{base}/delegated/contentaccess/books/{quote(product_id)}",
                    "method": "GET",
                    "headers": {"Authorization": "Bearer delegated-fake"},
                    "query": {"DisplayProfile": profile},
                },
                "session_context": {
                    "app_version": "fake-1.0", "device_id": "device-fake-001",
                    "user_id": "user-fake-001", "serial_number": "serial-fake-001",
                },
                "refresh_token_exposed": False,
                "content_access_response": {
                    "may_include_content_keys": drm_type == "KDRM",
                    "content_keys_values_returned_by_vault": False,
                    "consumer_must_classify_drm": True,
                },
                "renewal": {"strategy": "resolve_again", "endpoint": f"/api/v2/library/{product_id}/acquisition"},
            }
        self.send_json(handler, HTTPStatus.OK, {
            "provider": "kobo",
            "schema_version": "1.4",
            "item": self.item_json(handler, row, library=True),
            "acquisition": acquisition,
        })

    def delegated_content_access(self, handler, product_id: str, qs):
        row = self.cache.get(product_id)
        if row is None or row["owned_order"] is None:
            return self.error(handler, HTTPStatus.NOT_FOUND, "not_found", "livre absent du Vault")
        self.send_json(handler, HTTPStatus.OK, {
            "product_id": product_id,
            "display_profile": (qs.get("DisplayProfile") or ["Android"])[0],
            "drm_type": row["drm_type"],
            "content_keys": [],
            "note": "Réponse fictive : aucune clé DRM réelle n'est exposée.",
        })
    def store_page(self, handler, product_id: str):
        row = self.cache.get(product_id)
        if row is None:
            return self.error(handler, HTTPStatus.NOT_FOUND, "not_found", "livre inconnu")
        body = f'''<!doctype html><html lang="fr"><meta charset="utf-8"><title>{esc(row['title'])}</title>
<body><h1>{esc(row['title'])}</h1><p><b>{esc(row['author'])}</b> — {esc(row['publisher'])}</p>
<p>{esc(row['description'])}</p><p>Donnée fictive Kobo pour tests Cifereca.</p></body></html>'''.encode("utf-8")
        self.send_bytes(handler, HTTPStatus.OK, "text/html; charset=utf-8", body)

    def cover(self, handler, product_id: str):
        row = self.cache.get(product_id)
        if row is None:
            return self.error(handler, HTTPStatus.NOT_FOUND, "not_found", "livre inconnu")
        if self.asset_is_broken(row):
            return self.error(handler, HTTPStatus.SERVICE_UNAVAILABLE, "simulated_failure", "panne de couverture simulée")
        path = self.cache.cover_path(product_id)
        if not path.exists():
            path.write_bytes(cover_png_bytes(self._book_from_row(row), width=480, height=720))
        self.send_bytes(handler, HTTPStatus.OK, "image/png", path.read_bytes(), cache_control="public, max-age=86400")

    def epub(self, handler, product_id: str, *, kepub: bool = False):
        row = self.cache.get(product_id)
        if row is None:
            return self.error(handler, HTTPStatus.NOT_FOUND, "not_found", "livre inconnu")
        if self.asset_is_broken(row):
            return self.error(handler, HTTPStatus.SERVICE_UNAVAILABLE, "simulated_failure", "panne de téléchargement simulée")
        path = self.cache.epub_path(product_id)
        if not path.exists():
            self.write_epub(path, row)
        ctype = "application/epub+zip"
        self.send_bytes(handler, HTTPStatus.OK, ctype, path.read_bytes(), cache_control="public, max-age=86400")

    def acsm(self, handler, product_id: str):
        row = self.cache.get(product_id)
        if row is None:
            return self.error(handler, HTTPStatus.NOT_FOUND, "not_found", "livre inconnu")
        if self.asset_is_broken(row):
            return self.error(handler, HTTPStatus.SERVICE_UNAVAILABLE, "simulated_failure", "panne ACSM simulée")
        path = self.cache.acsm_path(product_id)
        if not path.exists():
            xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<fulfillmentToken xmlns="http://ns.adobe.com/adept"><resource>{esc(product_id)}</resource>
<title>{esc(row['title'])}</title><note>ACSM fictif pour tests Cifereca, non fulfillable.</note></fulfillmentToken>'''
            path.write_text(xml, encoding="utf-8")
        self.send_bytes(handler, HTTPStatus.OK, "application/vnd.adobe.adept+xml", path.read_bytes(), cache_control="public, max-age=86400")

    @staticmethod
    def make_cover_svg(row: sqlite3.Row) -> str:
        bg, accent, fg = PALETTES[int(row["cover_variant"]) % len(PALETTES)]
        words = row["title"].split()
        lines, current = [], []
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
        title_nodes = "\n".join(
            f'<text x="600" y="{700+i*95}" text-anchor="middle" font-size="68" font-family="serif" font-weight="700" fill="{fg}">{esc(line)}</text>'
            for i, line in enumerate(lines[:4])
        )
        return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1600" viewBox="0 0 1200 1600">
<rect width="1200" height="1600" fill="{bg}"/><circle cx="930" cy="260" r="210" fill="{accent}" opacity=".92"/>
<rect x="120" y="180" width="18" height="1180" fill="{accent}"/>
<text x="600" y="250" text-anchor="middle" font-size="28" font-family="sans-serif" letter-spacing="6" fill="{fg}">KOBO — TEST</text>
{title_nodes}<line x1="350" y1="1200" x2="850" y2="1200" stroke="{accent}" stroke-width="5"/>
<text x="600" y="1300" text-anchor="middle" font-size="42" font-family="sans-serif" fill="{fg}">{esc(row['author'])}</text>
<text x="600" y="1460" text-anchor="middle" font-size="22" font-family="sans-serif" fill="{fg}" opacity=".75">DONNÉE FICTIVE — CIFERECA</text></svg>'''

    @staticmethod
    def chapter_html(row: sqlite3.Row) -> str:
        rng = random.Random(stable_int(row["product_id"], row["title"]))
        weather = rng.choice(["une pluie fine rayait les vitres", "le brouillard descendait sur les toits", "un vent salé remontait les rues", "le soleil pâle allongeait les ombres"])
        sound = rng.choice(["le grincement d'une enseigne", "le souffle régulier des vagues", "le claquement d'une porte", "le sifflement d'un train"])
        paragraphs = [
            f"À {row['place']}, {weather}. Le jour commençait comme si rien ne devait changer.",
            f"Camille trouva pourtant un {row['object_name']} posé devant sa porte, accompagné d'une enveloppe sans adresse.",
            f"Elle relut le titre inscrit sur la couverture : « {row['title']} ». Au loin, {sound} interrompit le silence.",
            "Elle comprit que l'objet n'était pas arrivé là par hasard et décida de suivre le premier indice avant la tombée du jour.",
            "Ce chapitre est un texte fictif généré automatiquement pour les tests de Cifereca.",
        ]
        return "\n".join(f"<p>{esc(p)}</p>" for p in paragraphs)

    @staticmethod
    def _book_from_row(row: sqlite3.Row) -> FakeBook:
        return FakeBook(
            index=int(row["book_id"]),
            identifier=f"urn:cifereca:kobo:{row['product_id']}",
            title=str(row["title"]),
            author=str(row["author"]),
            publisher=str(row["publisher"]),
            publication_date=str(row["publication_date"]),
            subject=str(row["subject"]),
            language=str(row["language"]),
            place=str(row["place"]),
            object_name=str(row["object_name"]),
            description=str(row["description"]),
        )

    def write_epub(self, path: Path, row: sqlite3.Row):
        write_shared_epub(path, self._book_from_row(row))

    def route(self, handler: BaseHTTPRequestHandler):
        parsed = urlsplit(handler.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query, keep_blank_values=True)

        if path == "/":
            return self.root(handler)
        if path == "/health":
            return self.health(handler)
        if path == "/api/v2/status":
            return self.status(handler)

        # Assets/public pages are deliberately public for UI tests.
        m = re.fullmatch(r"/store/books/([^/]+)", path)
        if m:
            return self.store_page(handler, m.group(1))
        m = re.fullmatch(r"/assets/covers/([^/]+)\.png", path)
        if m:
            return self.cover(handler, m.group(1))
        m = re.fullmatch(r"/assets/epub/([^/]+)\.epub", path)
        if m:
            return self.epub(handler, m.group(1))
        m = re.fullmatch(r"/assets/kepub/([^/]+)\.kepub", path)
        if m:
            return self.epub(handler, m.group(1), kepub=True)
        m = re.fullmatch(r"/assets/acsm/([^/]+)\.acsm", path)
        if m:
            return self.acsm(handler, m.group(1))

        # The delegated ContentAccess fixture advertises an Authorization
        # header to the consumer. Keep it private like the Vault API; a
        # synthetic endpoint must not teach clients that delegation is public.
        if (
            path.startswith("/api/v2/") or path.startswith("/delegated/")
        ) and not self.authenticated(handler):
            return

        if path == "/api/v2/account":
            return self.account(handler)
        if path == "/api/v2/catalog/search":
            return self.catalog_search(handler, qs)
        if path == "/api/v2/library/search":
            return self.library_search(handler, qs)
        if path == "/api/v2/library/manifest":
            if self.manifest_fault(handler, qs):
                return
            return self.manifest(handler, qs)

        m = re.fullmatch(r"/api/v2/library/([^/]+)/acquisition", path)
        if m:
            if handler.command != "POST":
                return self.error(handler, HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed", "POST requis")
            return self.acquisition(handler, m.group(1), handler.json_body or {})
        m = re.fullmatch(r"/api/v2/library/([^/]+)", path)
        if m:
            return self.library_item(handler, m.group(1))
        m = re.fullmatch(r"/delegated/contentaccess/books/([^/]+)", path)
        if m:
            return self.delegated_content_access(handler, m.group(1), qs)

        return self.error(handler, HTTPStatus.NOT_FOUND, "not_found", "route inconnue")


class Handler(BaseHTTPRequestHandler):
    server_version = "CiferecaFakeKobo/1.0"
    app: KoboFakeApp
    json_body: dict[str, object] | None = None

    def _prepare_body(self):
        self.json_body = None
        if self.command in {"POST", "PUT", "PATCH"}:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 1_000_000:
                raise ValueError("corps HTTP trop volumineux")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                value = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                raise ValueError("JSON invalide") from exc
            if not isinstance(value, dict):
                raise ValueError("objet JSON attendu")
            self.json_body = value

    def do_GET(self):
        self._dispatch()

    def do_HEAD(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def _dispatch(self):
        try:
            self._prepare_body()
            self.app.route(self)
        except (ValueError, OverflowError) as exc:
            self.app.error(self, HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
        except BrokenPipeError:
            pass
        except Exception as exc:
            print(f"[error] {self.command} {self.path}: {exc}", file=sys.stderr)
            try:
                self.app.error(self, HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", type(exc).__name__)
            except BrokenPipeError:
                pass

    def log_message(self, fmt: str, *args):
        sys.stderr.write("[http] %s - %s\n" % (self.address_string(), fmt % args))


def parse_args():
    p = argparse.ArgumentParser(description="Pseudo services Kobo Catalogue et Kobo Vault pour Cifereca")
    p.add_argument("--service", choices=("catalog", "vault", "combined"), default="combined")
    p.add_argument("--bind", default="127.0.0.1", help="Adresse d'écoute (défaut: 127.0.0.1)")
    p.add_argument("--port", type=int, default=8090, help="Port HTTP (défaut: 8090)")
    p.add_argument("--cache-dir", type=Path, default=Path(".kobo-fake-cache"), help="Répertoire du cache")
    p.add_argument("--catalog-books", type=int, default=DEFAULT_CATALOG_BOOKS, help="Taille catalogue (défaut: 10000)")
    p.add_argument("--vault-books", type=int, default=DEFAULT_VAULT_BOOKS, help="Livres possédés (défaut: 1000)")
    p.add_argument("--seed", type=int, default=20261006, help="Graine déterministe")
    p.add_argument("--token", default=DEFAULT_TOKEN, help="Bearer token attendu (défaut: kvt_test)")
    p.add_argument("--accept-any-token", action="store_true", help="Accepter tout Bearer token non vide")
    p.add_argument("--broken-assets-every", type=int, default=0, help="Simule une panne d'asset tous les N livres (0=désactivé)")
    p.add_argument("--rebuild", action="store_true", help="Reconstruire le cache")
    p.add_argument("--build-cache-only", action="store_true", help="Construire/valider le cache puis quitter")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.catalog_books < 1 or args.vault_books < 0:
        raise SystemExit("les tailles de JDD doivent être positives")
    if args.vault_books > args.catalog_books:
        raise SystemExit("--vault-books doit être <= --catalog-books")
    if not (1 <= args.port <= 65535):
        raise SystemExit("--port doit être compris entre 1 et 65535")
    if args.broken_assets_every < 0:
        raise SystemExit("--broken-assets-every doit être >= 0")

    cache = DatasetCache(args.cache_dir, args.catalog_books, args.vault_books, args.seed, args.rebuild)
    if args.build_cache_only:
        print(json.dumps(cache.counts(), ensure_ascii=False))
        return 0

    app = KoboFakeApp(
        cache,
        service=args.service,
        token=args.token,
        accept_any_token=args.accept_any_token,
        broken_assets_every=args.broken_assets_every,
    )
    Handler.app = app
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"[server] mode={args.service} http://{args.bind}:{args.port}/")
    if args.service in {"vault", "combined"}:
        print(f"[server] Vault base_url : http://{args.bind}:{args.port}")
        print(f"[server] token          : {args.token}")
    if args.service in {"catalog", "vault", "combined"}:
        print(f"[server] Catalogue      : http://{args.bind}:{args.port}/api/v2/catalog/search")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] arrêt")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())