#!/usr/bin/env python3
"""Pseudo service SRU BnF Catalogue général pour Cifereca.

JDD par défaut : 20 000 notices bibliographiques.

Le serveur reproduit le sous-ensemble SRU 1.2 consommé par
`provider.bnf-catalogue` :

    GET /api/SRU?operation=searchRetrieve&version=1.2
        &recordSchema=dublincore
        &query=bib.title+all+"..."
        &startRecord=1
        &maximumRecords=50

Champs CQL supportés :
- bib.anywhere
- bib.title
- bib.author
- bib.isbn
- bib.subject

Relations supportées :
- all
- adj

Le catalogue mélange notices physiques, numériques et support indéterminé afin
de tester les filtres du plugin. Les notices numériques peuvent exposer un lien
Gallica mais aucune acquisition n'est simulée.

Dépendances : Python 3, bibliothèque standard uniquement.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sqlite3
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree as ET

DATASET_ROOT = Path(__file__).resolve().parents[1]
if str(DATASET_ROOT) not in sys.path:
    sys.path.insert(0, str(DATASET_ROOT))

from common.fake_books import FakeBookFactory, stable_int

APP_VERSION = "1.1"
CACHE_SCHEMA_VERSION = "1"
DEFAULT_BOOKS = 20_000
DEFAULT_PORT = 8095
DEFAULT_SEED = 20261006
MAX_PAGE_SIZE = 100

SRU = "http://www.loc.gov/zing/srw/"
DC = "http://purl.org/dc/elements/1.1/"
DIAG = "http://www.loc.gov/zing/srw/diagnostic/"
CQL_RE = re.compile(
    r'^\s*(bib\.(?:anywhere|title|author|isbn|subject))\s+(all|adj)\s+"((?:\\.|[^"\\])*)"\s*$',
    re.I,
)

ET.register_namespace("srw", SRU)
ET.register_namespace("dc", DC)
ET.register_namespace("diag", DIAG)


def unfold(value: str) -> str:
    import unicodedata
    value = unicodedata.normalize("NFKD", value.casefold())
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def cql_unescape(value: str) -> str:
    out = []
    escaped = False
    for ch in value:
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        else:
            out.append(ch)
    if escaped:
        out.append("\\")
    return "".join(out)


class DatasetCache:
    def __init__(self, cache_dir: Path, books: int, seed: int, rebuild: bool = False) -> None:
        self.cache_dir = cache_dir
        self.books = books
        self.seed = seed
        self.db_path = cache_dir / "bnf-sru.sqlite3"
        self._local = threading.local()

        if rebuild and cache_dir.exists():
            shutil.rmtree(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
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
                ark TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                title_fold TEXT NOT NULL,
                author TEXT NOT NULL,
                author_fold TEXT NOT NULL,
                publisher TEXT NOT NULL,
                publication_date TEXT NOT NULL,
                subject TEXT NOT NULL,
                subject_fold TEXT NOT NULL,
                description TEXT NOT NULL,
                isbn TEXT NOT NULL,
                language TEXT NOT NULL,
                support TEXT NOT NULL,
                mime_type TEXT,
                bibliographic_format TEXT,
                gallica_url TEXT,
                rights TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_bnf_title_fold ON books(title_fold);
            CREATE INDEX IF NOT EXISTS idx_bnf_author_fold ON books(author_fold);
            CREATE INDEX IF NOT EXISTS idx_bnf_subject_fold ON books(subject_fold);
            CREATE INDEX IF NOT EXISTS idx_bnf_isbn ON books(isbn);
            CREATE INDEX IF NOT EXISTS idx_bnf_support ON books(support);
            """
        )

        meta = dict(conn.execute("SELECT key,value FROM meta").fetchall())
        count = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        valid = (
            meta.get("schema_version") == CACHE_SCHEMA_VERSION
            and meta.get("app_version") == APP_VERSION
            and meta.get("books") == str(self.books)
            and meta.get("seed") == str(self.seed)
            and count == self.books
        )
        if valid:
            print(f"[cache] Réutilisation {self.db_path} — books={count}")
            conn.close()
            return

        print(f"[cache] Génération BnF SRU : {self.books} notices…")
        conn.execute("DELETE FROM books")
        conn.execute("DELETE FROM meta")

        factory = FakeBookFactory(self.seed, "bnf-sru")
        batch = []
        for index in range(1, self.books + 1):
            book = factory.make(index)
            roll = stable_int(self.seed, "support", index) % 100
            if roll < 56:
                support = "physical"
                mime = None
                bib_format = f"1 vol. ({120 + stable_int(index, 'pages') % 780} p.) ; {18 + stable_int(index, 'cm') % 13} cm"
                gallica = None
                rights = "Notice bibliographique publique"
            elif roll < 88:
                support = "digital"
                mime = "application/epub+zip" if stable_int(self.seed, "mime", index) % 100 < 72 else "application/pdf"
                bib_format = mime
                gallica = (
                    f"https://gallica.bnf.fr/ark:/12148/bpt6k{8_000_000 + index}"
                    if stable_int(self.seed, "gallica", index) % 100 < 72
                    else None
                )
                rights = "Ressource numérique fictive — accès non garanti"
            else:
                support = "unknown"
                mime = None
                bib_format = None
                gallica = None
                rights = "Notice bibliographique publique"

            ark = f"ark:/12148/cb{50_000_000 + index:08d}{chr(97 + index % 26)}"
            isbn = f"978{stable_int(self.seed, 'isbn', index) % 10_000_000_000:010d}"
            batch.append(
                (
                    index,
                    ark,
                    book.title,
                    unfold(book.title),
                    book.author,
                    unfold(book.author),
                    book.publisher,
                    book.publication_date[:4],
                    book.subject,
                    unfold(book.subject),
                    book.description,
                    isbn,
                    book.language,
                    support,
                    mime,
                    bib_format,
                    gallica,
                    rights,
                )
            )
            if len(batch) >= 1000:
                conn.executemany("INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                batch.clear()
        if batch:
            conn.executemany("INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)

        conn.executemany(
            "INSERT INTO meta(key,value) VALUES (?,?)",
            [
                ("schema_version", CACHE_SCHEMA_VERSION),
                ("app_version", APP_VERSION),
                ("books", str(self.books)),
                ("seed", str(self.seed)),
            ],
        )
        conn.commit()
        conn.close()
        print(f"[cache] Prêt : {self.db_path}")

    def count(self) -> int:
        return self.connect().execute("SELECT COUNT(*) FROM books").fetchone()[0]

    def search(self, field: str, relation: str, value: str, start: int, maximum: int) -> tuple[int, list[sqlite3.Row]]:
        conn = self.connect()
        normalized = unfold(value.strip())

        if field == "bib.isbn":
            isbn = re.sub(r"[\s-]", "", value)
            where = "isbn = ?"
            params: list[object] = [isbn]
        else:
            column = {
                "bib.title": "title_fold",
                "bib.author": "author_fold",
                "bib.subject": "subject_fold",
            }.get(field)

            if field == "bib.anywhere":
                columns = ["title_fold", "author_fold", "subject_fold", "isbn"]
            elif column:
                columns = [column]
            else:
                raise ValueError("unsupported field")

            terms = [t for t in normalized.split() if t]
            if not terms:
                return 0, []

            if relation == "adj":
                expr = "(" + " OR ".join(f"{c} LIKE ?" for c in columns) + ")"
                where = expr
                params = [f"%{normalized}%"] * len(columns)
            else:
                term_exprs = []
                params = []
                for term in terms:
                    term_exprs.append("(" + " OR ".join(f"{c} LIKE ?" for c in columns) + ")")
                    params.extend([f"%{term}%"] * len(columns))
                where = " AND ".join(term_exprs)

        total = conn.execute(f"SELECT COUNT(*) FROM books WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM books WHERE {where} ORDER BY book_id LIMIT ? OFFSET ?",
            [*params, maximum, max(0, start - 1)],
        ).fetchall()
        return total, rows


class BnfSruApp:
    def __init__(self, cache: DatasetCache):
        self.cache = cache

    @staticmethod
    def send_bytes(handler, status: int, content_type: str, body: bytes) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)

    def send_xml(self, handler, root: ET.Element, status: int = HTTPStatus.OK) -> None:
        body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        self.send_bytes(handler, status, "application/xml; charset=utf-8", body)

    def diagnostic(self, handler, message: str, uri: str = "info:srw/diagnostic/1/10") -> None:
        root = ET.Element(f"{{{SRU}}}searchRetrieveResponse")
        ET.SubElement(root, f"{{{SRU}}}version").text = "1.2"
        diagnostics = ET.SubElement(root, f"{{{SRU}}}diagnostics")
        diag = ET.SubElement(diagnostics, f"{{{DIAG}}}diagnostic")
        ET.SubElement(diag, f"{{{DIAG}}}uri").text = uri
        ET.SubElement(diag, f"{{{DIAG}}}message").text = message
        self.send_xml(handler, root)

    @staticmethod
    def dc(parent: ET.Element, name: str, value: str | None) -> None:
        if value:
            ET.SubElement(parent, f"{{{DC}}}{name}").text = value

    def record_xml(self, row: sqlite3.Row) -> ET.Element:
        record = ET.Element(f"{{{SRU}}}record")
        ET.SubElement(record, f"{{{SRU}}}recordSchema").text = "info:srw/schema/1/dc-v1.1"
        ET.SubElement(record, f"{{{SRU}}}recordPacking").text = "xml"
        record_data = ET.SubElement(record, f"{{{SRU}}}recordData")
        dc_root = ET.SubElement(record_data, f"{{{DC}}}dc")

        self.dc(dc_root, "title", row["title"])
        self.dc(dc_root, "creator", row["author"])
        self.dc(dc_root, "publisher", row["publisher"])
        self.dc(dc_root, "date", row["publication_date"])
        self.dc(dc_root, "subject", row["subject"])
        self.dc(dc_root, "description", row["description"])
        self.dc(dc_root, "language", row["language"])
        self.dc(dc_root, "identifier", row["ark"])
        self.dc(dc_root, "identifier", f"urn:isbn:{row['isbn']}")
        self.dc(dc_root, "rights", row["rights"])

        if row["support"] == "digital":
            self.dc(dc_root, "type", "Ressource électronique")
            self.dc(dc_root, "format", row["mime_type"])
        elif row["support"] == "physical":
            self.dc(dc_root, "type", "Texte imprimé")
            self.dc(dc_root, "format", row["bibliographic_format"])
        else:
            self.dc(dc_root, "type", "Texte")

        # Structured values exercise the series/collection projection of the plugin.
        self.dc(dc_root, "relation", f"Série {row['subject']}")
        self.dc(dc_root, "source", f"Collection {row['subject']}")
        self.dc(dc_root, "source", f"Collection éditeur {row['publisher']}")
        if row["gallica_url"]:
            self.dc(dc_root, "relation", row["gallica_url"])
        return record

    def search_retrieve(self, handler, qs):
        operation = (qs.get("operation") or [""])[0]
        version = (qs.get("version") or [""])[0]
        schema = (qs.get("recordSchema") or [""])[0]
        query = (qs.get("query") or [""])[0]

        if operation != "searchRetrieve":
            return self.diagnostic(handler, "Unsupported operation", "info:srw/diagnostic/1/4")
        if version not in {"", "1.2"}:
            return self.diagnostic(handler, "Unsupported version", "info:srw/diagnostic/1/5")
        if schema not in {"", "dublincore"}:
            return self.diagnostic(handler, "Unsupported record schema", "info:srw/diagnostic/1/66")

        match = CQL_RE.fullmatch(query)
        if not match:
            return self.diagnostic(handler, "Unsupported or malformed CQL query", "info:srw/diagnostic/1/10")

        field, relation, raw_value = match.groups()
        field = field.casefold()
        relation = relation.casefold()
        value = cql_unescape(raw_value)

        try:
            start = int((qs.get("startRecord") or ["1"])[0])
            maximum = int((qs.get("maximumRecords") or ["10"])[0])
        except ValueError:
            return self.diagnostic(handler, "Invalid paging parameter", "info:srw/diagnostic/1/6")
        if start < 1 or not (1 <= maximum <= MAX_PAGE_SIZE):
            return self.diagnostic(handler, "Paging parameter out of range", "info:srw/diagnostic/1/6")

        total, rows = self.cache.search(field, relation, value, start, maximum)

        root = ET.Element(f"{{{SRU}}}searchRetrieveResponse")
        ET.SubElement(root, f"{{{SRU}}}version").text = "1.2"
        ET.SubElement(root, f"{{{SRU}}}numberOfRecords").text = str(total)
        records = ET.SubElement(root, f"{{{SRU}}}records")
        for index, row in enumerate(rows, start=start):
            record = self.record_xml(row)
            ET.SubElement(record, f"{{{SRU}}}recordPosition").text = str(index)
            records.append(record)

        if rows and start - 1 + len(rows) < total:
            ET.SubElement(root, f"{{{SRU}}}nextRecordPosition").text = str(start + len(rows))
        self.send_xml(handler, root)

    def root(self, handler):
        payload = {
            "name": "Cifereca fake BnF SRU",
            "version": APP_VERSION,
            "books": self.cache.count(),
            "endpoint": "/api/SRU",
            "fields": ["bib.anywhere", "bib.title", "bib.author", "bib.isbn", "bib.subject"],
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_bytes(handler, HTTPStatus.OK, "application/json; charset=utf-8", body)

    def health(self, handler):
        body = json.dumps({"ok": True, "books": self.cache.count()}, ensure_ascii=False).encode("utf-8")
        self.send_bytes(handler, HTTPStatus.OK, "application/json; charset=utf-8", body)

    def route(self, handler):
        parsed = urlsplit(handler.path)
        if parsed.path == "/":
            return self.root(handler)
        if parsed.path == "/health":
            return self.health(handler)
        if parsed.path.rstrip("/") == "/api/SRU":
            return self.search_retrieve(handler, parse_qs(parsed.query, keep_blank_values=True))
        self.send_bytes(handler, HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"route inconnue")


class Handler(BaseHTTPRequestHandler):
    server_version = "CiferecaFakeBnfSRU/1.0"
    app: BnfSruApp

    def do_GET(self):
        try:
            self.app.route(self)
        except BrokenPipeError:
            pass
        except Exception as exc:
            print(f"[error] GET {self.path}: {type(exc).__name__}: {exc}", file=sys.stderr)
            try:
                self.app.send_bytes(self, HTTPStatus.INTERNAL_SERVER_ERROR, "text/plain", type(exc).__name__.encode())
            except BrokenPipeError:
                pass

    def do_HEAD(self):
        self.do_GET()

    def log_message(self, fmt: str, *args):
        sys.stderr.write("[http] %s - %s\n" % (self.address_string(), fmt % args))


def parse_args():
    p = argparse.ArgumentParser(description="Pseudo service SRU BnF Catalogue général")
    p.add_argument("--bind", default="127.0.0.1", help="Adresse d'écoute")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port HTTP (défaut: {DEFAULT_PORT})")
    p.add_argument("--cache-dir", type=Path, default=Path(".bnf-sru-fake-cache"), help="Répertoire du cache")
    p.add_argument("--books", type=int, default=DEFAULT_BOOKS, help="Nombre de notices")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Graine déterministe")
    p.add_argument("--rebuild", action="store_true", help="Reconstruire entièrement le cache")
    p.add_argument("--build-cache-only", action="store_true", help="Générer/valider le cache puis quitter")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.books < 1:
        raise SystemExit("--books doit être >= 1")
    if not (1 <= args.port <= 65535):
        raise SystemExit("--port invalide")

    cache = DatasetCache(args.cache_dir, args.books, args.seed, args.rebuild)
    if args.build_cache_only:
        print(json.dumps({"books": cache.count()}, ensure_ascii=False))
        return 0

    Handler.app = BnfSruApp(cache)
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"[server] http://{args.bind}:{args.port}/api/SRU")
    print(f"[server] Cifereca sru_url : http://{args.bind}:{args.port}/api/SRU")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] arrêt")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
