#!/usr/bin/env python3
"""
pseudo_paris_service.py — faux service Bibliothèques de Paris / PNB pour Cifereca.

Le serveur reproduit les routes HTTP réellement consommées par provider.paris :
- authentification Syracuse ;
- compte principal + comptes liés ;
- catalogue physique et numérique ;
- disponibilités / holdings ;
- prêts, historique et réservations ;
- emprunt PNB via ASSARedirect ;
- téléchargement ACSM fictif ;
- renouvellement et retour.

JDD par défaut :
- 10 000 notices (70 % numériques / 30 % physiques) ;
- 120 prêts actifs répartis entre 3 comptes ;
- 200 prêts historiques ;
- 30 réservations physiques.

Le catalogue et les états utilisateur sont persistés dans SQLite. Les couvertures
PNG et ACSM sont générés à la demande et mis en cache.

Exemples :
    python3 pseudo_paris_service.py
    python3 pseudo_paris_service.py --bind 0.0.0.0 --port 8093
    python3 pseudo_paris_service.py --rebuild
    python3 pseudo_paris_service.py --build-cache-only

Configuration Cifereca :
    base_url = http://127.0.0.1:8093
    username = paris-test
    password = test

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
import struct
import sys
import threading
import unicodedata
import zlib
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

DATASET_ROOT = Path(__file__).resolve().parents[1]
if str(DATASET_ROOT) not in sys.path:
    sys.path.insert(0, str(DATASET_ROOT))

from common.fake_books import FakeBook, FakeBookFactory, cover_png_bytes

APP_VERSION = "1.1"
CACHE_SCHEMA_VERSION = "1"
DEFAULT_BOOKS = 10_000
DEFAULT_ACTIVE_LOANS = 120
DEFAULT_HISTORY = 200
DEFAULT_BOOKINGS = 30
DEFAULT_DIGITAL_RATIO = 0.70
DEFAULT_USERNAME = "paris-test"
DEFAULT_PASSWORD = "test"
DEFAULT_PORT = 8093
SEARCH_MAX = 50

ACCOUNT_SERVICE = "/numerique/Portal/Services/UserAccountService.svc"
LOGIN_PATH = "/Portal/Recherche/logon.svc/logon"
SEARCH_PATH = "/Portal/Recherche/Search.svc/Search"
HOLDINGS_PATH = "/Portal/Services/ILSClient.svc/GetListHoldings"
CHECK_AVAILABILITY_PATH = "/Portal/Services/ILSClient.svc/CheckAvailability"

ACCOUNTS = [
    ("", "Compte principal", "750000000001"),
    ("linked-1", "Compte famille 1", "750000000002"),
    ("linked-2", "Compte famille 2", "750000000003"),
]

FIRST_NAMES = [
    "Adèle", "Alice", "Bastien", "Camille", "Diane", "Élias", "Émile", "Inès",
    "Jules", "Lina", "Maël", "Nora", "Oscar", "Salomé", "Théo", "Victor",
    "Yasmine", "Zoé", "Lucien", "Madeleine", "Auguste", "Céleste", "Anaïs",
    "Noé", "Léonie", "Martin", "Iris", "Gabriel", "Louise", "Simon",
]
LAST_NAMES = [
    "Armand", "Beaulieu", "Caron", "Delmas", "Faure", "Garnier", "Hébert",
    "Lefèvre", "Moreau", "Perrin", "Roche", "Valette", "Vidal", "Bernier",
    "Clément", "Dumont", "Fournier", "Girard", "Laurent", "Marchand", "Renaud",
    "Boulanger", "Lenoir", "Aubert", "Deschamps", "Renard",
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
MOTIFS = [
    "des ombres", "des étoiles", "des marées", "des oiseaux", "des cendres",
    "des lucioles", "des cartes", "des lettres", "des voix", "des saisons",
    "des chemins", "des fenêtres", "des îles", "des souvenirs", "des voyageurs",
]
MOMENTS = [
    "Avant l'aube", "Après minuit", "Au dernier hiver", "Un soir de septembre",
    "À l'heure des marées", "Au printemps des absents", "Quand revient le vent",
    "Le jour des lucioles", "À la fin de l'été", "Sous la première neige",
]
SUBJECTS = [
    "Roman", "Mystère", "Aventure", "Fantastique", "Histoire", "Voyage",
    "Poésie", "Science-fiction", "Fiction contemporaine", "Enquête", "Nature",
    "Arts", "Musique", "Mémoire", "Société", "Correspondance", "Exploration",
]
LIBRARIES = [
    "Bibliothèque Marguerite Duras", "Bibliothèque François Villon",
    "Médiathèque Françoise Sagan", "Bibliothèque Forney",
    "Bibliothèque André Malraux", "Bibliothèque Marguerite Yourcenar",
    "Bibliothèque Robert Sabatier", "Bibliothèque Violette Leduc",
]
TITLE_PATTERNS = [
    "Le {object} — {place}",
    "Le {object} {qualifier}",
    "Le {object} {qualifier} — {place}",
    "La mémoire — {place}",
    "La mémoire {qualifier}",
    "Les nuits — {place}",
    "Les nuits {qualifier}",
    "Sous le ciel, {place}",
    "Sous le ciel {qualifier}",
    "Le secret — {place}",
    "Le secret du {object}",
    "Une saison — {place}",
    "Une saison {qualifier}",
    "Chroniques — {place}",
    "Chroniques du {object} {qualifier}",
    "L'ombre du {object}",
    "L'ombre sur {place}",
    "Le dernier {object}",
    "Le dernier {object} — {place}",
    "{moment} — {place}",
    "{moment}, le {object}",
    "La route {motif}",
    "Le livre {motif}",
    "Ce que racontent {motif}",
    "Là où dorment {motif}",
    "Quand disparaît le {object}",
    "Carnet de voyage : {place}",
    "Les archives {qualifier}",
    "La chambre {qualifier}",
    "Le passage {motif}",
]

PALETTES = [
    ((28, 52, 84), (244, 211, 94)),
    ((59, 31, 71), (224, 122, 95)),
    ((20, 54, 66), (236, 154, 41)),
    ((79, 45, 63), (217, 180, 74)),
    ((29, 53, 87), (230, 57, 70)),
    ((38, 70, 83), (233, 196, 106)),
    ((41, 50, 65), (238, 108, 77)),
    ((43, 45, 66), (239, 35, 60)),
]


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def stable_int(*parts: object) -> int:
    digest = hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ms_date(value: date | datetime) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day, 12, tzinfo=timezone.utc)
    elif value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return f"/Date({int(value.timestamp() * 1000)})/"


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower() or "book"


def make_title(rng: random.Random, used: set[str], index: int) -> tuple[str, str, str]:
    for _ in range(200):
        place = rng.choice(PLACES)
        obj = rng.choice(OBJECTS)
        title = rng.choice(TITLE_PATTERNS).format(
            object=obj,
            place=place,
            qualifier=rng.choice(QUALIFIERS),
            motif=rng.choice(MOTIFS),
            moment=rng.choice(MOMENTS),
        )
        if title not in used:
            used.add(title)
            return title, place, obj
    title = f"Chronique parisienne fictive — {index:05d}"
    used.add(title)
    return title, rng.choice(PLACES), rng.choice(OBJECTS)


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def make_png(seed: int, width: int = 240, height: int = 360) -> bytes:
    bg, accent = PALETTES[seed % len(PALETTES)]
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            stripe = 22 < x < 30 or (x - width * 3 // 4) ** 2 + (y - height // 5) ** 2 < (width // 6) ** 2
            band = height * 2 // 3 < y < height * 2 // 3 + 5
            color = accent if stripe or band else bg
            raw.extend(color)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + png_chunk(b"IEND", b"")
    )


class DatasetCache:
    def __init__(
        self,
        cache_dir: Path,
        books: int,
        active_loans: int,
        history_items: int,
        bookings: int,
        digital_ratio: float,
        seed: int,
        rebuild: bool = False,
    ) -> None:
        self.cache_dir = cache_dir
        self.books = books
        self.active_loans_count = active_loans
        self.history_items = history_items
        self.bookings = bookings
        self.digital_ratio = digital_ratio
        self.seed = seed
        self.db_path = cache_dir / "paris.sqlite3"
        self.cover_dir = cache_dir / "covers"
        self.acsm_dir = cache_dir / "acsm"
        self._local = threading.local()

        if rebuild and cache_dir.exists():
            shutil.rmtree(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.cover_dir.mkdir(parents=True, exist_ok=True)
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
        conn.execute("PRAGMA foreign_keys=ON")
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
                record_id TEXT UNIQUE NOT NULL,
                docbase TEXT NOT NULL,
                medium TEXT NOT NULL,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                publisher TEXT NOT NULL,
                publication_date TEXT NOT NULL,
                subject TEXT NOT NULL,
                description TEXT NOT NULL,
                isbn TEXT NOT NULL,
                format TEXT,
                language TEXT NOT NULL,
                place TEXT NOT NULL,
                object_name TEXT NOT NULL,
                cover_variant INTEGER NOT NULL,
                licenses INTEGER NOT NULL,
                borrowable INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS loans (
                loan_id TEXT PRIMARY KEY,
                account_uid TEXT NOT NULL,
                provider TEXT NOT NULL,
                record_id TEXT NOT NULL,
                borrowed_at TEXT NOT NULL,
                due_at TEXT NOT NULL,
                renewed INTEGER NOT NULL DEFAULT 0,
                returned_at TEXT,
                FOREIGN KEY(record_id) REFERENCES books(record_id)
            );
            CREATE TABLE IF NOT EXISTS bookings (
                booking_id TEXT PRIMARY KEY,
                account_uid TEXT NOT NULL,
                record_id TEXT NOT NULL,
                booking_date TEXT NOT NULL,
                rank INTEGER NOT NULL,
                state TEXT NOT NULL,
                cancelled INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(record_id) REFERENCES books(record_id)
            );
            CREATE INDEX IF NOT EXISTS idx_books_medium ON books(medium);
            CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);
            CREATE INDEX IF NOT EXISTS idx_books_author ON books(author);
            CREATE INDEX IF NOT EXISTS idx_loans_account ON loans(account_uid, returned_at);
            CREATE INDEX IF NOT EXISTS idx_loans_record ON loans(record_id, returned_at);
            """
        )
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        counts = {
            "books": conn.execute("SELECT COUNT(*) FROM books").fetchone()[0],
            "active": conn.execute("SELECT COUNT(*) FROM loans WHERE returned_at IS NULL").fetchone()[0],
            "history": conn.execute("SELECT COUNT(*) FROM loans WHERE returned_at IS NOT NULL").fetchone()[0],
            "bookings": conn.execute("SELECT COUNT(*) FROM bookings WHERE cancelled=0").fetchone()[0],
        }
        valid = (
            meta.get("schema_version") == CACHE_SCHEMA_VERSION
            and meta.get("app_version") == APP_VERSION
            and meta.get("books") == str(self.books)
            and meta.get("active_loans") == str(self.active_loans_count)
            and meta.get("history_items") == str(self.history_items)
            and meta.get("bookings") == str(self.bookings)
            and meta.get("digital_ratio") == str(self.digital_ratio)
            and meta.get("seed") == str(self.seed)
            and counts["books"] == self.books
            and counts["active"] == self.active_loans_count
            and counts["history"] == self.history_items
            and counts["bookings"] == self.bookings
        )
        if valid:
            print(f"[cache] Réutilisation {self.db_path} — {counts}")
            conn.close()
            return

        print(f"[cache] Génération catalogue Paris : {self.books} notices…")
        conn.execute("DELETE FROM bookings")
        conn.execute("DELETE FROM loans")
        conn.execute("DELETE FROM books")
        conn.execute("DELETE FROM meta")

        rng = random.Random(stable_int(self.seed, "paris-service"))
        factory = FakeBookFactory(self.seed, "paris")
        digital_target = int(round(self.books * self.digital_ratio))
        base_date = date(1985, 1, 1)
        span = (date(2026, 9, 30) - base_date).days
        rows = []
        for book_id in range(1, self.books + 1):
            digital = book_id <= digital_target
            book = factory.make(book_id)
            pub = base_date + timedelta(days=rng.randrange(span + 1))
            isbn = f"978{stable_int(self.seed, 'isbn', book_id) % 10_000_000_000:010d}"
            record_id = isbn if digital else f"PARIS-{book_id:06d}"
            rows.append(
                (
                    book_id,
                    record_id,
                    "DILICOM" if digital else "SYRACUSE",
                    "digital" if digital else "physical",
                    book.title,
                    book.author,
                    book.publisher,
                    pub.isoformat(),
                    book.subject,
                    book.description,
                    isbn,
                    "EPUB" if digital else None,
                    book.language,
                    book.place,
                    book.object_name,
                    rng.randrange(len(PALETTES)),
                    rng.choice([1, 1, 1, 2, 2, 3, 4]) if digital else rng.choice([1, 2, 3]),
                    0 if stable_int(self.seed, "borrowable", book_id) % 100 < 4 else 1,
                )
            )
            if len(rows) >= 1000:
                conn.executemany("INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                rows.clear()
        if rows:
            conn.executemany("INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()

        digital_ids = [r[0] for r in conn.execute("SELECT record_id FROM books WHERE medium='digital'").fetchall()]
        physical_ids = [r[0] for r in conn.execute("SELECT record_id FROM books WHERE medium='physical'").fetchall()]
        loan_rng = random.Random(stable_int(self.seed, "loans"))
        account_uids = [x[0] for x in ACCOUNTS]

        active_rows = []
        selected_active: set[tuple[str, str]] = set()
        while len(active_rows) < self.active_loans_count:
            provider = "DILICOM" if loan_rng.random() < 0.80 and digital_ids else "SYRACUSE"
            record_id = loan_rng.choice(digital_ids if provider == "DILICOM" else physical_ids)
            uid = loan_rng.choices(account_uids, weights=[6, 2, 2], k=1)[0]
            key = (uid, record_id)
            if key in selected_active:
                continue
            selected_active.add(key)
            borrowed = date(2026, 9, 1) + timedelta(days=loan_rng.randrange(30))
            due = borrowed + timedelta(days=28 if provider == "DILICOM" else 21)
            active_rows.append(
                (
                    f"loan-{len(active_rows)+1:05d}",
                    uid,
                    provider,
                    record_id,
                    borrowed.isoformat(),
                    due.isoformat(),
                    1 if loan_rng.random() < 0.18 else 0,
                    None,
                )
            )
        conn.executemany("INSERT INTO loans VALUES (?,?,?,?,?,?,?,?)", active_rows)

        history_rows = []
        for i in range(self.history_items):
            provider = "DILICOM" if loan_rng.random() < 0.70 and digital_ids else "SYRACUSE"
            record_id = loan_rng.choice(digital_ids if provider == "DILICOM" else physical_ids)
            uid = loan_rng.choices(account_uids, weights=[6, 2, 2], k=1)[0]
            returned = date(2025, 1, 1) + timedelta(days=loan_rng.randrange(600))
            borrowed = returned - timedelta(days=loan_rng.randrange(7, 35))
            history_rows.append(
                (
                    f"hist-{i+1:05d}",
                    uid,
                    provider,
                    record_id,
                    borrowed.isoformat(),
                    returned.isoformat(),
                    1 if loan_rng.random() < 0.12 else 0,
                    returned.isoformat(),
                )
            )
        conn.executemany("INSERT INTO loans VALUES (?,?,?,?,?,?,?,?)", history_rows)

        booking_rows = []
        for i in range(self.bookings):
            record_id = loan_rng.choice(physical_ids)
            uid = loan_rng.choices(account_uids, weights=[6, 2, 2], k=1)[0]
            booking_date = date(2026, 9, 20) + timedelta(days=loan_rng.randrange(16))
            booking_rows.append(
                (f"booking-{i+1:04d}", uid, record_id, booking_date.isoformat(), 1 + loan_rng.randrange(12), "active", 0)
            )
        conn.executemany("INSERT INTO bookings VALUES (?,?,?,?,?,?,?)", booking_rows)

        conn.executemany(
            "INSERT INTO meta(key,value) VALUES (?,?)",
            [
                ("schema_version", CACHE_SCHEMA_VERSION),
                ("app_version", APP_VERSION),
                ("books", str(self.books)),
                ("active_loans", str(self.active_loans_count)),
                ("history_items", str(self.history_items)),
                ("bookings", str(self.bookings)),
                ("digital_ratio", str(self.digital_ratio)),
                ("seed", str(self.seed)),
                ("generated_at", iso_now()),
            ],
        )
        conn.commit()
        conn.close()
        print(f"[cache] Prêt : {self.db_path}")

    def counts(self) -> dict[str, int]:
        c = self.connect()
        return {
            "catalog": c.execute("SELECT COUNT(*) FROM books").fetchone()[0],
            "digital": c.execute("SELECT COUNT(*) FROM books WHERE medium='digital'").fetchone()[0],
            "physical": c.execute("SELECT COUNT(*) FROM books WHERE medium='physical'").fetchone()[0],
            "active_loans": c.execute("SELECT COUNT(*) FROM loans WHERE returned_at IS NULL").fetchone()[0],
            "history": c.execute("SELECT COUNT(*) FROM loans WHERE returned_at IS NOT NULL").fetchone()[0],
            "bookings": c.execute("SELECT COUNT(*) FROM bookings WHERE cancelled=0").fetchone()[0],
        }

    def book(self, record_id: str) -> sqlite3.Row | None:
        return self.connect().execute("SELECT * FROM books WHERE record_id=?", (record_id,)).fetchone()

    def search(self, scenario: str, query: str, page: int, limit: int) -> list[sqlite3.Row]:
        c = self.connect()
        medium = "digital" if scenario.upper() == "EBOOKS" else "physical"
        terms = [x.casefold() for x in re.split(r"\s+", str(query or "").strip()) if x]
        where = ["medium=?"]
        params: list[object] = [medium]
        expr = "lower(title || ' ' || author || ' ' || publisher || ' ' || subject || ' ' || isbn || ' ' || record_id)"
        for term in terms:
            where.append(expr + " LIKE ?")
            params.append("%" + term + "%")
        limit = max(1, min(SEARCH_MAX, int(limit)))
        offset = max(0, int(page)) * limit
        return c.execute(
            f"SELECT * FROM books WHERE {' AND '.join(where)} ORDER BY book_id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    def active_loans(self, account_uid: str, provider: str) -> list[sqlite3.Row]:
        return self.connect().execute(
            """SELECT l.*, b.title, b.author, b.publisher, b.publication_date, b.format, b.medium, b.cover_variant
               FROM loans l JOIN books b ON b.record_id=l.record_id
               WHERE l.account_uid=? AND l.provider=? AND l.returned_at IS NULL
               ORDER BY l.due_at, b.title""",
            (account_uid, provider),
        ).fetchall()

    def history(self, account_uid: str, provider: str) -> list[sqlite3.Row]:
        return self.connect().execute(
            """SELECT l.*, b.title, b.author, b.publication_date, b.medium, b.cover_variant
               FROM loans l JOIN books b ON b.record_id=l.record_id
               WHERE l.account_uid=? AND l.provider=? AND l.returned_at IS NOT NULL
               ORDER BY l.returned_at DESC LIMIT 500""",
            (account_uid, provider),
        ).fetchall()

    def booking_rows(self, account_uid: str, provider: str) -> list[sqlite3.Row]:
        if provider != "SYRACUSE":
            return []
        return self.connect().execute(
            """SELECT r.*, b.title FROM bookings r JOIN books b ON b.record_id=r.record_id
               WHERE r.account_uid=? AND r.cancelled=0 ORDER BY r.booking_date DESC""",
            (account_uid,),
        ).fetchall()

    def active_count(self, record_id: str) -> int:
        return self.connect().execute(
            "SELECT COUNT(*) FROM loans WHERE record_id=? AND returned_at IS NULL", (record_id,)
        ).fetchone()[0]

    def borrow(self, record_id: str, account_uid: str = "") -> str:
        c = self.connect()
        row = self.book(record_id)
        if row is None or row["medium"] != "digital":
            raise ValueError("livre numérique inconnu")
        existing = c.execute(
            "SELECT loan_id FROM loans WHERE record_id=? AND account_uid=? AND returned_at IS NULL",
            (record_id, account_uid),
        ).fetchone()
        if existing:
            return str(existing[0])
        if not row["borrowable"] or self.active_count(record_id) >= int(row["licenses"]):
            raise ValueError("ouvrage indisponible")
        seq = c.execute("SELECT COUNT(*) FROM loans").fetchone()[0] + 1
        loan_id = f"loan-live-{seq:06d}"
        borrowed = date.today()
        due = borrowed + timedelta(days=28)
        c.execute(
            "INSERT INTO loans VALUES (?,?,?,?,?,?,?,NULL)",
            (loan_id, account_uid, "DILICOM", record_id, borrowed.isoformat(), due.isoformat(), 0),
        )
        c.commit()
        return loan_id

    def renew(self, loan_id: str, account_uid: str) -> bool:
        c = self.connect()
        row = c.execute(
            "SELECT due_at, renewed FROM loans WHERE loan_id=? AND account_uid=? AND returned_at IS NULL",
            (loan_id, account_uid),
        ).fetchone()
        if row is None or row["renewed"]:
            return False
        due = date.fromisoformat(row["due_at"]) + timedelta(days=21)
        c.execute("UPDATE loans SET due_at=?, renewed=1 WHERE loan_id=?", (due.isoformat(), loan_id))
        c.commit()
        return True

    def return_loan(self, loan_id: str, account_uid: str) -> bool:
        c = self.connect()
        row = c.execute(
            "SELECT provider FROM loans WHERE loan_id=? AND account_uid=? AND returned_at IS NULL",
            (loan_id, account_uid),
        ).fetchone()
        if row is None or row["provider"] != "DILICOM":
            return False
        c.execute("UPDATE loans SET returned_at=? WHERE loan_id=?", (date.today().isoformat(), loan_id))
        c.commit()
        return True

    def cancel_booking(self, booking_id: str, account_uid: str) -> bool:
        c = self.connect()
        cur = c.execute(
            "UPDATE bookings SET cancelled=1 WHERE booking_id=? AND account_uid=? AND cancelled=0",
            (booking_id, account_uid),
        )
        c.commit()
        return cur.rowcount > 0

    def cover_path(self, record_id: str) -> Path:
        return self.cover_dir / f"{slugify(record_id)}.png"

    def acsm_path(self, record_id: str) -> Path:
        return self.acsm_dir / f"{slugify(record_id)}.acsm"


class ParisFakeApp:
    def __init__(self, cache: DatasetCache, username: str, password: str):
        self.cache = cache
        self.username = username
        self.password = password

    @staticmethod
    def base(handler: BaseHTTPRequestHandler) -> str:
        host = handler.headers.get("Host") or f"127.0.0.1:{handler.server.server_port}"
        proto = handler.headers.get("X-Forwarded-Proto", "http").split(",", 1)[0].strip() or "http"
        return f"{proto}://{host}"

    @staticmethod
    def send_bytes(
        handler: BaseHTTPRequestHandler,
        status: int,
        content_type: str,
        body: bytes,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            handler.send_header(key, value)
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)

    def send_json(self, handler, obj: object, status: int = HTTPStatus.OK, headers: dict[str, str] | None = None):
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_bytes(handler, status, "application/json; charset=utf-8", body, headers=headers)

    def success(self, handler, d: object | None = None):
        payload: dict[str, object] = {"success": True}
        if d is not None:
            payload["d"] = d
        self.send_json(handler, payload)

    def fail(self, handler, message: str, status: int = HTTPStatus.BAD_REQUEST):
        self.send_json(handler, {"success": False, "message": message, "errors": [{"message": message}]}, status)

    def root(self, handler):
        self.send_json(
            handler,
            {
                "name": "Cifereca fake Bibliothèques de Paris / PNB",
                "version": APP_VERSION,
                "credentials": {"username": self.username, "password": self.password},
                "counts": self.cache.counts(),
                "base_url": self.base(handler),
            },
        )

    def health(self, handler):
        self.send_json(handler, {"ok": True, "counts": self.cache.counts()})

    def login(self, handler):
        form = handler.form_body or {}
        username = str(form.get("username") or "")
        password = str(form.get("password") or "")
        if username != self.username or password != self.password:
            return self.send_json(handler, {"success": False, "message": "Identifiant ou mot de passe incorrect"}, HTTPStatus.UNAUTHORIZED)
        self.send_json(
            handler,
            {"success": True, "d": {"DisplayName": "Compte Paris fictif"}},
            headers={"Set-Cookie": "CIFERECA_PARIS_FAKE=1; Path=/; HttpOnly; SameSite=Lax"},
        )

    def summary(self, handler, qs):
        uid = (qs.get("userUniqueIdentifier") or [""])[0]
        account = next((x for x in ACCOUNTS if x[0] == uid), ACCOUNTS[0])
        active = sum(
            len(self.cache.active_loans(uid, provider)) for provider in ("DILICOM", "SYRACUSE")
        )
        self.success(
            handler,
            {
                "AccountSummary": {
                    "DisplayName": account[1],
                    "Barcode": account[2],
                    "LoansCount": active,
                    "BookingsCount": len(self.cache.booking_rows(uid, "SYRACUSE")),
                    "MessagesCount": 0,
                }
            },
        )

    def pairings(self, handler):
        rows = [
            {
                "State": 2,
                "UserUniqueIdentifier": uid,
                "UserDisplayName": label,
                "UserBarcode": barcode,
            }
            for uid, label, barcode in ACCOUNTS
            if uid
        ]
        self.success(handler, {"UserPairings": rows, "UserReversePairings": []})

    def cover_url(self, handler, record_id: str) -> str:
        return f"{self.base(handler)}/fake-assets/covers/{quote(record_id)}.png"

    def acsm_url(self, handler, record_id: str) -> str:
        return f"{self.base(handler)}/fake-delivery/{quote(record_id)}.acsm"

    def search_result(self, handler, row: sqlite3.Row, scenario: str) -> dict[str, object]:
        digital = row["medium"] == "digital"
        cover = self.cover_url(handler, row["record_id"])
        resource: dict[str, object] = {
            "RscId": row["record_id"],
            "RscBase": row["docbase"],
            "Ttl": row["title"],
            "Crtr": row["author"],
            "Type": "Livre numérique" if digital else "Livre",
            "Pbls": row["publisher"],
            "Dt": row["publication_date"][:4],
            "Desc": row["description"],
            "Language": row["language"],
            "ISBN": row["isbn"],
        }
        if digital:
            resource["MediaFormat"] = "EPUB"
        else:
            resource["Frmt"] = "MONO"
        return {
            "Resource": resource,
            "AdditionalProperties": {
                "MediaFormat": "EPUB" if digital else None,
                "Language": row["language"],
            },
            "CompactResult": f'<div class="notice"><img src="{esc(cover)}" alt="couverture"></div>',
            "CustomResult": "",
            "FriendlyUrl": f"{self.base(handler)}/fake-record/{quote(row['record_id'])}",
            "ScenarioCode": scenario,
        }

    def search(self, handler):
        body = handler.json_body or {}
        query_obj = body.get("query") if isinstance(body.get("query"), dict) else {}
        scenario = str(query_obj.get("ScenarioCode") or "CATALOGUE").upper()
        if scenario not in {"CATALOGUE", "EBOOKS"}:
            return self.fail(handler, "ScenarioCode inconnu")
        query = str(query_obj.get("QueryString") or "")
        page = int(query_obj.get("Page") or 0)
        limit = int(query_obj.get("ResultSize") or 20)
        rows = self.cache.search(scenario, query, page, limit)
        self.success(handler, {"Results": [self.search_result(handler, row, scenario) for row in rows]})

    def holding_data(self, handler, row: sqlite3.Row) -> tuple[list[dict[str, object]], dict[str, object], str]:
        if row["medium"] == "digital":
            active = self.cache.active_count(row["record_id"])
            available = bool(row["borrowable"]) and active < int(row["licenses"])
            label = (
                f"Disponible — {int(row['licenses']) - active} licence(s)"
                if available
                else "Indisponible — toutes les licences sont utilisées"
            )
            item = {
                "Availability": 1 if available else 0,
                "CanLoan": available,
                "HoldingLabel": label,
            }
            html_view = ""
            if available:
                nested = f"/fake/loan-book.ashx?ean={quote(row['record_id'])}"
                href = f"/numerique/ASSARedirect.ashx?url={quote(nested, safe='')}"
                html_view = f'<a class="pnb-loan" href="{esc(href)}">Emprunter</a>'
            return [], item, html_view

        rng = random.Random(stable_int(self.cache.seed, "holdings", row["record_id"]))
        holdings: list[dict[str, object]] = []
        for index in range(1 + rng.randrange(3)):
            library = rng.choice(LIBRARIES)
            available = rng.random() < 0.68
            holdings.append(
                {
                    "HoldingId": f"{row['record_id']}-{index+1}",
                    "HoldingPlace": library,
                    "Section": rng.choice(["Adultes", "Littérature", "Documentaires"]),
                    "Localisation": rng.choice(["Rez-de-chaussée", "1er étage", "Salle de lecture"]),
                    "Cote": f"{rng.randrange(100,999)} {row['author'].split()[-1][:3].upper()}",
                    "Statut": "Disponible" if available else "Emprunté",
                    "IsAvailable": available,
                    "IsLoanable": bool(row["borrowable"]),
                    "IsReservable": True,
                    "NbResa": rng.randrange(6),
                    "WhenBack": None if available else ms_date(date.today() + timedelta(days=rng.randrange(2, 21))),
                }
            )
        can = any(bool(x["IsAvailable"]) and bool(x["IsLoanable"]) for x in holdings)
        return holdings, {"Availability": 1 if can else 0, "CanLoan": can, "HoldingLabel": "Disponible" if can else "Indisponible"}, ""

    def holdings(self, handler):
        body = handler.json_body or {}
        record = body.get("Record") if isinstance(body.get("Record"), dict) else {}
        record_id = str(record.get("RscId") or "")
        row = self.cache.book(record_id)
        if row is None:
            return self.fail(handler, "Notice inconnue", HTTPStatus.NOT_FOUND)
        holdings, item, html_view = self.holding_data(handler, row)
        self.success(
            handler,
            {
                "fieldList": {
                    "Title": row["title"],
                    "Author_sort": row["author"],
                    "TypeOfDocument": "Livre numérique" if row["medium"] == "digital" else "Livre",
                    "YearOfPublication": row["publication_date"][:4],
                    "ThumbMedium": self.cover_url(handler, row["record_id"]),
                    "ThumbSmall": self.cover_url(handler, row["record_id"]),
                },
                "Holdings": holdings,
                "ItemHoldingsData": item,
                "HtmlView": html_view,
            },
        )

    def loan_json(self, handler, row: sqlite3.Row) -> dict[str, object]:
        digital = row["provider"] == "DILICOM"
        additional: dict[str, object] = {
            "Auteur": row["author"],
            "Annee": row["publication_date"][:4],
            "CanReturn": digital,
            "AccessMedium": "PNB" if digital else "Bibliothèque",
            "HasBeenRenewed": bool(row["renewed"]),
        }
        if digital:
            additional["MediaFormat"] = "EPUB"
            additional["EbookUrl"] = f"/fake-delivery/{quote(row['record_id'])}.acsm"
        return {
            "Id": row["loan_id"],
            "HoldingId": row["loan_id"],
            "RecordId": row["record_id"],
            "Title": row["title"],
            "ThumbnailUrl": self.cover_url(handler, row["record_id"]),
            "DefaultThumbnailUrl": self.cover_url(handler, row["record_id"]),
            "TypeOfDocument": "Livre numérique" if digital else "Livre",
            "WhenBack": ms_date(date.fromisoformat(row["due_at"])),
            "Location": None if digital else "Bibliothèques de Paris",
            "State": "loan",
            "IsLate": date.fromisoformat(row["due_at"]) < date.today(),
            "IsSoonLate": date.today() <= date.fromisoformat(row["due_at"]) <= date.today() + timedelta(days=3),
            "CanRenew": not bool(row["renewed"]),
            "CannotRenewReason": "Déjà renouvelé" if row["renewed"] else None,
            "AdditionalProperties": additional,
        }

    def list_loans(self, handler, qs):
        provider = (qs.get("serviceCode") or ["SYRACUSE"])[0].upper()
        uid = (qs.get("userUniqueIdentifier") or [""])[0]
        rows = self.cache.active_loans(uid, provider)
        self.success(handler, {"Loans": [self.loan_json(handler, row) for row in rows]})

    def list_history(self, handler, qs):
        provider = (qs.get("serviceCode") or ["SYRACUSE"])[0].upper()
        uid = (qs.get("userUniqueIdentifier") or [""])[0]
        rows = self.cache.history(uid, provider)
        payload = []
        for row in rows:
            payload.append(
                {
                    "HoldingId": row["loan_id"],
                    "RecordId": row["record_id"],
                    "Title": row["title"],
                    "ThumbnailUrl": self.cover_url(handler, row["record_id"]) if provider == "DILICOM" else None,
                    "DefaultThumbnailUrl": self.cover_url(handler, row["record_id"]),
                    "TypeOfDocument": "Livre numérique" if provider == "DILICOM" else "Livre",
                    "LoanDate": ms_date(date.fromisoformat(row["borrowed_at"])),
                    "HandingDate": ms_date(date.fromisoformat(row["returned_at"])),
                    "AdditionalProperties": {
                        "Auteur": row["author"],
                        "Annee": row["publication_date"][:4],
                    },
                }
            )
        self.success(handler, {"Handings": payload})

    def list_bookings(self, handler, qs):
        provider = (qs.get("serviceCode") or ["SYRACUSE"])[0].upper()
        uid = (qs.get("userUniqueIdentifier") or [""])[0]
        rows = self.cache.booking_rows(uid, provider)
        payload = []
        for row in rows:
            when = date.fromisoformat(row["booking_date"])
            payload.append(
                {
                    "Id": row["booking_id"],
                    "HoldingId": row["booking_id"],
                    "RecordId": row["record_id"],
                    "Title": row["title"],
                    "BookingDate": ms_date(when),
                    "AvailabilityDate": None,
                    "AvailableUntilDate": None,
                    "LocationLabel": "Bibliothèques de Paris",
                    "Rank": row["rank"],
                    "State": row["state"],
                }
            )
        self.success(handler, {"Bookings": payload})

    def redirect_borrow(self, handler, qs):
        nested = (qs.get("url") or [""])[0]
        nested_qs = parse_qs(urlsplit(unquote(nested)).query)
        record_id = (nested_qs.get("ean") or [""])[0]
        row = self.cache.book(record_id)
        if row is None or row["medium"] != "digital":
            return self.fail(handler, "EAN PNB inconnu", HTTPStatus.NOT_FOUND)
        try:
            self.cache.borrow(record_id, "")
        except ValueError as exc:
            return self.fail(handler, str(exc), HTTPStatus.CONFLICT)
        location = f"/fake-delivery/{quote(record_id)}.acsm"
        self.send_bytes(handler, HTTPStatus.FOUND, "text/plain; charset=utf-8", b"", headers={"Location": location})

    def preferences(self, handler):
        self.send_json(
            handler,
            {
                "success": True,
                "PassPhrase": "paris-fake-passphrase",
                "Hint": "jeu de donnees",
                "ReaderId": "urn:uuid:cifereca-paris-fake-reader",
                "ReaderAppName": "Cifereca Fake ADE",
            },
        )

    def download_digital_document(self, handler):
        body = handler.json_body or {}
        record_id = str(body.get("ean") or "")
        row = self.cache.book(record_id)
        if row is None or row["medium"] != "digital":
            return self.fail(handler, "Document numérique inconnu", HTTPStatus.NOT_FOUND)
        self.success(handler, f"/fake-delivery/{quote(record_id)}.acsm")

    def renew(self, handler):
        body = handler.json_body or {}
        uid = str(body.get("userUniqueIdentifier") or "")
        loans = body.get("loans") if isinstance(body.get("loans"), list) else []
        loan_id = ""
        if loans and isinstance(loans[0], dict):
            loan_id = str(loans[0].get("Id") or loans[0].get("HoldingId") or "")
        if not loan_id or not self.cache.renew(loan_id, uid):
            return self.fail(handler, "Prêt non renouvelable", HTTPStatus.CONFLICT)
        self.success(handler, {"Renewed": True, "LoanId": loan_id})

    def return_loan(self, handler):
        body = handler.json_body or {}
        uid = str(body.get("userUniqueIdentifier") or "")
        loan_id = str(body.get("id") or "")
        if not loan_id or not self.cache.return_loan(loan_id, uid):
            return self.fail(handler, "Prêt numérique introuvable", HTTPStatus.CONFLICT)
        self.success(handler, {"Returned": True, "LoanId": loan_id})

    def cancel_booking(self, handler):
        body = handler.json_body or {}
        uid = str(body.get("userUniqueIdentifier") or "")
        rows = body.get("bookings") if isinstance(body.get("bookings"), list) else []
        booking_id = ""
        if rows and isinstance(rows[0], dict):
            booking_id = str(rows[0].get("Id") or rows[0].get("HoldingId") or "")
        if not booking_id or not self.cache.cancel_booking(booking_id, uid):
            return self.fail(handler, "Réservation introuvable", HTTPStatus.CONFLICT)
        self.success(handler, {"Cancelled": True, "BookingId": booking_id})

    def check_availability(self, handler):
        body = handler.json_body or {}
        records = body.get("RecordIdArray") if isinstance(body.get("RecordIdArray"), list) else []
        out = []
        for raw in records:
            if not isinstance(raw, dict):
                continue
            record_id = str(raw.get("RscId") or raw.get("RecordId") or "")
            row = self.cache.book(record_id)
            if row is None:
                continue
            available = bool(row["borrowable"])
            if row["medium"] == "digital":
                available = available and self.cache.active_count(record_id) < int(row["licenses"])
            out.append({"RecordId": record_id, "IsAvailable": available})
        self.success(handler, out)

    def cover(self, handler, record_id: str):
        row = self.cache.book(record_id)
        if row is None:
            return self.fail(handler, "Notice inconnue", HTTPStatus.NOT_FOUND)
        path = self.cache.cover_path(record_id)
        if not path.exists():
            book = FakeBook(
                index=int(row["book_id"]),
                identifier=f"urn:cifereca:paris:{record_id}",
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
            path.write_bytes(cover_png_bytes(book, width=240, height=360))
        self.send_bytes(handler, HTTPStatus.OK, "image/png", path.read_bytes())

    def acsm(self, handler, record_id: str):
        row = self.cache.book(record_id)
        if row is None or row["medium"] != "digital":
            return self.fail(handler, "Document numérique inconnu", HTTPStatus.NOT_FOUND)
        path = self.cache.acsm_path(record_id)
        if not path.exists():
            token = f"""<?xml version="1.0" encoding="UTF-8"?>
<fulfillmentToken xmlns="http://ns.adobe.com/adept">
  <resource>{esc(record_id)}</resource>
  <title>{esc(row['title'])}</title>
  <operatorURL>{esc(self.base(handler))}</operatorURL>
  <note>ACSM fictif pour tests Cifereca. Aucun DRM réel.</note>
</fulfillmentToken>
"""
            path.write_text(token, encoding="utf-8")
        self.send_bytes(handler, HTTPStatus.OK, "application/vnd.adobe.adept+xml", path.read_bytes())

    def record_page(self, handler, record_id: str):
        row = self.cache.book(record_id)
        if row is None:
            return self.fail(handler, "Notice inconnue", HTTPStatus.NOT_FOUND)
        body = (
            "<!doctype html><html lang='fr'><meta charset='utf-8'>"
            f"<title>{esc(row['title'])}</title><body><h1>{esc(row['title'])}</h1>"
            f"<p><b>{esc(row['author'])}</b> — {esc(row['publisher'])}</p>"
            f"<p>{esc(row['description'])}</p><p>Donnée fictive pour tests Cifereca.</p></body></html>"
        ).encode("utf-8")
        self.send_bytes(handler, HTTPStatus.OK, "text/html; charset=utf-8", body)

    def route(self, handler: BaseHTTPRequestHandler):
        parsed = urlsplit(handler.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query, keep_blank_values=True)

        # P0 #771: opt-in, stateless failures on existing PNB routes only.
        # Reject before any loan/return/renewal mutation or ACSM materialization.
        fault_routes = {
            f"{ACCOUNT_SERVICE}/ListLoans",
            f"{ACCOUNT_SERVICE}/DownloadDigitalDocument",
            f"{ACCOUNT_SERVICE}/RenewLoans",
            f"{ACCOUNT_SERVICE}/ReturnDigitalLoan",
            "/numerique/ASSARedirect.ashx",
        }
        faults = qs.get("ux_fault", [])
        if path in fault_routes and len(faults) == 1 and faults[0] in {
            "expired", "429", "503",
        }:
            fault = faults[0]
            code, label = {
                "expired": (HTTPStatus.UNAUTHORIZED, "Session fictive expirée"),
                "429": (HTTPStatus.TOO_MANY_REQUESTS, "Limite fictive atteinte"),
                "503": (HTTPStatus.SERVICE_UNAVAILABLE, "Service fictif indisponible"),
            }[fault]
            payload = {"success": False, "message": label, "errors": [{"message": label}]}
            return self.send_json(
                handler, payload, code,
                headers={"Retry-After": "2"} if fault == "429" else None,
            )

        if path == "/":
            return self.root(handler)
        if path == "/health":
            return self.health(handler)
        if path == "/numerique":
            return self.send_bytes(handler, HTTPStatus.OK, "text/html; charset=utf-8", b"<html><body>Paris fake</body></html>")
        if path == LOGIN_PATH and handler.command == "POST":
            return self.login(handler)
        if path == SEARCH_PATH and handler.command == "POST":
            return self.search(handler)
        if path == HOLDINGS_PATH and handler.command == "POST":
            return self.holdings(handler)
        if path == CHECK_AVAILABILITY_PATH and handler.command == "POST":
            return self.check_availability(handler)

        if path == f"{ACCOUNT_SERVICE}/RetrieveAccountSummary":
            return self.summary(handler, qs)
        if path == f"{ACCOUNT_SERVICE}/ListUserPairings":
            return self.pairings(handler)
        if path == f"{ACCOUNT_SERVICE}/ListLoans":
            return self.list_loans(handler, qs)
        if path == f"{ACCOUNT_SERVICE}/ListHandings":
            return self.list_history(handler, qs)
        if path == f"{ACCOUNT_SERVICE}/ListBookings":
            return self.list_bookings(handler, qs)
        if path == f"{ACCOUNT_SERVICE}/GetUserPnbPreferences":
            return self.preferences(handler)
        if path == f"{ACCOUNT_SERVICE}/SavePnbPreferences":
            return self.preferences(handler)
        if path == f"{ACCOUNT_SERVICE}/DownloadDigitalDocument" and handler.command == "POST":
            return self.download_digital_document(handler)
        if path == f"{ACCOUNT_SERVICE}/RenewLoans" and handler.command == "POST":
            return self.renew(handler)
        if path == f"{ACCOUNT_SERVICE}/ReturnDigitalLoan" and handler.command == "POST":
            return self.return_loan(handler)
        if path == f"{ACCOUNT_SERVICE}/CancelBookings" and handler.command == "POST":
            return self.cancel_booking(handler)

        if path == "/numerique/ASSARedirect.ashx":
            return self.redirect_borrow(handler, qs)

        m = re.fullmatch(r"/fake-assets/covers/([^/]+)\.png", path)
        if m:
            return self.cover(handler, unquote(m.group(1)))
        m = re.fullmatch(r"/fake-delivery/([^/]+)\.acsm", path)
        if m:
            return self.acsm(handler, unquote(m.group(1)))
        m = re.fullmatch(r"/fake-record/([^/]+)", path)
        if m:
            return self.record_page(handler, unquote(m.group(1)))

        return self.fail(handler, "Route inconnue", HTTPStatus.NOT_FOUND)


class Handler(BaseHTTPRequestHandler):
    server_version = "CiferecaFakeParis/1.0"
    app: ParisFakeApp
    json_body: dict[str, object] | None = None
    form_body: dict[str, str] | None = None

    def _prepare_body(self):
        self.json_body = None
        self.form_body = None
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2_000_000:
            raise ValueError("corps HTTP trop volumineux")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if content_type == "application/json":
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("objet JSON attendu")
            self.json_body = value
        elif content_type == "application/x-www-form-urlencoded":
            parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
            self.form_body = {k: v[-1] if v else "" for k, v in parsed.items()}

    def _dispatch(self):
        try:
            self._prepare_body()
            self.app.route(self)
        except (ValueError, OverflowError, json.JSONDecodeError) as exc:
            self.app.fail(self, str(exc), HTTPStatus.BAD_REQUEST)
        except BrokenPipeError:
            pass
        except Exception as exc:
            print(f"[error] {self.command} {self.path}: {type(exc).__name__}: {exc}", file=sys.stderr)
            try:
                self.app.fail(self, type(exc).__name__, HTTPStatus.INTERNAL_SERVER_ERROR)
            except BrokenPipeError:
                pass

    def do_GET(self):
        self._dispatch()

    def do_HEAD(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def log_message(self, fmt: str, *args):
        sys.stderr.write("[http] %s - %s\n" % (self.address_string(), fmt % args))


def parse_args():
    p = argparse.ArgumentParser(description="Pseudo service Bibliothèques de Paris / PNB pour Cifereca")
    p.add_argument("--bind", default="127.0.0.1", help="Adresse d'écoute")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port HTTP (défaut: {DEFAULT_PORT})")
    p.add_argument("--cache-dir", type=Path, default=Path(".paris-fake-cache"), help="Répertoire du cache")
    p.add_argument("--books", type=int, default=DEFAULT_BOOKS, help="Nombre de notices catalogue")
    p.add_argument("--active-loans", type=int, default=DEFAULT_ACTIVE_LOANS, help="Nombre de prêts actifs initiaux")
    p.add_argument("--history-items", type=int, default=DEFAULT_HISTORY, help="Nombre de prêts historiques")
    p.add_argument("--bookings", type=int, default=DEFAULT_BOOKINGS, help="Nombre de réservations")
    p.add_argument("--digital-ratio", type=float, default=DEFAULT_DIGITAL_RATIO, help="Part numérique du catalogue")
    p.add_argument("--seed", type=int, default=20261006, help="Graine déterministe")
    p.add_argument("--username", default=DEFAULT_USERNAME, help="Identifiant accepté")
    p.add_argument("--password", default=DEFAULT_PASSWORD, help="Mot de passe accepté")
    p.add_argument("--rebuild", action="store_true", help="Reconstruire entièrement le cache")
    p.add_argument("--build-cache-only", action="store_true", help="Générer/valider le cache puis quitter")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.books < 10:
        raise SystemExit("--books doit être >= 10")
    if args.active_loans < 0 or args.history_items < 0 or args.bookings < 0:
        raise SystemExit("les volumes d'état doivent être >= 0")
    if not (0.05 <= args.digital_ratio <= 0.95):
        raise SystemExit("--digital-ratio doit être compris entre 0.05 et 0.95")
    if not (1 <= args.port <= 65535):
        raise SystemExit("--port invalide")

    cache = DatasetCache(
        args.cache_dir,
        args.books,
        args.active_loans,
        args.history_items,
        args.bookings,
        args.digital_ratio,
        args.seed,
        args.rebuild,
    )
    if args.build_cache_only:
        print(json.dumps(cache.counts(), ensure_ascii=False))
        return 0

    Handler.app = ParisFakeApp(cache, args.username, args.password)
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"[server] http://{args.bind}:{args.port}/")
    print(f"[server] Cifereca base_url : http://{args.bind}:{args.port}")
    print(f"[server] Login             : {args.username} / {args.password}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] arrêt")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
