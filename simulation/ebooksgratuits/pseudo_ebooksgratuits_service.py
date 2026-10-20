#!/usr/bin/env python3
"""Pseudo service Ebooks libres et gratuits pour les tests Cifereca."""
from __future__ import annotations

import argparse
import html
import json
import shutil
import sqlite3
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

DATASET_ROOT = Path(__file__).resolve().parents[1]
if str(DATASET_ROOT) not in sys.path:
    sys.path.insert(0, str(DATASET_ROOT))

from common.fake_books import FakeBook, FakeBookFactory, write_epub

APP_VERSION = "1.0"
CACHE_SCHEMA_VERSION = "1"
DEFAULT_BOOKS = 10_000
DEFAULT_PORT = 8096
DEFAULT_SEED = 20261006
GENRES = ("Roman", "Nouvelle", "Essai", "Poésie", "Aventure")


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


class DatasetCache:
    def __init__(self, cache_dir: Path, books: int, seed: int, rebuild: bool = False) -> None:
        self.cache_dir = cache_dir
        self.books = int(books)
        self.seed = int(seed)
        self.db_path = cache_dir / "ebooksgratuits.sqlite3"
        self.epub_dir = cache_dir / "epub"
        self._local = threading.local()
        if rebuild and cache_dir.exists():
            shutil.rmtree(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.epub_dir.mkdir(parents=True, exist_ok=True)
        self._prepare()

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def _prepare(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS books (
                book_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                publisher TEXT NOT NULL,
                publication_date TEXT NOT NULL,
                category TEXT NOT NULL,
                genre TEXT NOT NULL,
                language TEXT NOT NULL,
                place TEXT NOT NULL,
                object_name TEXT NOT NULL,
                description TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_eg_title ON books(title);
            CREATE INDEX IF NOT EXISTS idx_eg_author ON books(author);
            CREATE INDEX IF NOT EXISTS idx_eg_category ON books(category);
            CREATE INDEX IF NOT EXISTS idx_eg_genre ON books(genre);
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
            conn.close()
            return

        conn.execute("DELETE FROM books")
        conn.execute("DELETE FROM meta")
        factory = FakeBookFactory(self.seed, "ebooksgratuits")
        batch = []
        for index in range(1, self.books + 1):
            book = factory.make(index)
            genre = GENRES[(index - 1) % len(GENRES)]
            batch.append((
                index, book.title, book.author, book.publisher, book.publication_date,
                book.subject, genre, book.language, book.place, book.object_name, book.description,
            ))
            if len(batch) >= 1000:
                conn.executemany("INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?,?)", batch)
                batch.clear()
        if batch:
            conn.executemany("INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?,?)", batch)
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

    def count(self) -> int:
        return int(self.connect().execute("SELECT COUNT(*) FROM books").fetchone()[0])

    def get(self, book_id: int):
        return self.connect().execute("SELECT * FROM books WHERE book_id=?", (int(book_id),)).fetchone()

    def distinct(self, column: str) -> list[str]:
        if column not in {"author", "category", "genre"}:
            raise ValueError("unsupported filter")
        return [str(row[0]) for row in self.connect().execute(
            f"SELECT DISTINCT {column} FROM books ORDER BY {column}"
        ).fetchall()]

    def search(self, *, title: str = "", author: str = "", category: str = "", genre: str = "", offset: int = 0, limit: int = 50):
        where = []
        params: list[object] = []
        if title.strip():
            where.append("lower(title) LIKE ?")
            params.append("%" + title.strip().casefold() + "%")
        for column, value in (("author", author), ("category", category), ("genre", genre)):
            if value:
                where.append(f"{column} = ?")
                params.append(value)
        clause = " WHERE " + " AND ".join(where) if where else ""
        conn = self.connect()
        total = int(conn.execute("SELECT COUNT(*) FROM books" + clause, params).fetchone()[0])
        rows = conn.execute(
            "SELECT * FROM books" + clause + " ORDER BY book_id LIMIT ? OFFSET ?",
            [*params, max(1, min(200, int(limit))), max(0, int(offset))],
        ).fetchall()
        return total, rows

    def epub_path(self, book_id: int) -> Path:
        return self.epub_dir / f"{int(book_id)}.epub"

    @staticmethod
    def as_book(row: sqlite3.Row) -> FakeBook:
        return FakeBook(
            index=int(row["book_id"]),
            identifier=f"urn:cifereca:fake:ebooksgratuits:{row['book_id']}",
            title=str(row["title"]),
            author=str(row["author"]),
            publisher=str(row["publisher"]),
            publication_date=str(row["publication_date"]),
            subject=str(row["category"]),
            language=str(row["language"]),
            place=str(row["place"]),
            object_name=str(row["object_name"]),
            description=str(row["description"]),
        )


class EbooksGratuitsApp:
    def __init__(self, cache: DatasetCache):
        self.cache = cache

    @staticmethod
    def _send(handler, status: int, content_type: str, body: bytes, *, headers: dict[str, str] | None = None) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            handler.send_header(name, value)
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)

    def send_text(self, handler, status: int, content_type: str, text: str, *, headers: dict[str, str] | None = None) -> None:
        self._send(handler, status, content_type + "; charset=utf-8", text.encode("utf-8"), headers=headers)

    def send_json(self, handler, payload: dict, status: int = HTTPStatus.OK) -> None:
        self.send_text(handler, status, "application/json", json.dumps(payload, ensure_ascii=False))

    @staticmethod
    def _option_rows(values: list[str], *, all_value: str) -> str:
        rows = [f'<option value="{esc(all_value)}">Tous</option>']
        start = 1 if all_value == "0" else 2
        rows.extend(f'<option value="{idx}">{esc(value)}</option>' for idx, value in enumerate(values, start=start))
        return "".join(rows)

    def _filters(self):
        return {
            "author": self.cache.distinct("author"),
            "category": self.cache.distinct("category"),
            "genre": self.cache.distinct("genre"),
        }

    @staticmethod
    def _selected(values: list[str], raw: str, *, all_values: set[str]) -> str:
        if raw in all_values or not raw:
            return ""
        try:
            index = int(raw)
        except ValueError:
            return ""
        start = 1 if "0" in all_values else 2
        pos = index - start
        return values[pos] if 0 <= pos < len(values) else ""

    def page(self, handler, *, title: str = "", author_id: str = "0", category_id: str = "1", genre_id: str = "1", offset: int = 0, limit: int = 50):
        filters = self._filters()
        author = self._selected(filters["author"], author_id, all_values={"0"})
        category = self._selected(filters["category"], category_id, all_values={"1"})
        genre = self._selected(filters["genre"], genre_id, all_values={"1"})
        total, rows = self.cache.search(title=title, author=author, category=category, genre=genre, offset=offset, limit=limit)
        cards = []
        for row in rows:
            book_id = int(row["book_id"])
            cards.append(f'''<li>
<span class="listeb">{esc(row["title"])}</span>
<span class="auteur">{esc(row["author"])}</span>
<p class="resume">{esc(row["description"])}</p>
<a href="details.php?id={book_id}">Détails</a>
<a href="newsendbook.php?id={book_id}&amp;format=epub">EPUB</a>
</li>''')
        body = f'''<!doctype html><html lang="fr"><meta charset="utf-8"><title>Ebooks gratuits fictif</title>
<body>
<form method="get" action="ebooks.php">
<select name="id_auteur">{self._option_rows(filters["author"], all_value="0")}</select>
<select name="id_categorie">{self._option_rows(filters["category"], all_value="1")}</select>
<select name="id_genre">{self._option_rows(filters["genre"], all_value="1")}</select>
</form>
<p class="count">{total} résultat(s)</p><ul>{"".join(cards)}</ul>
</body></html>'''
        self.send_text(handler, HTTPStatus.OK, "text/html", body)

    def details(self, handler, book_id: int):
        row = self.cache.get(book_id)
        if row is None:
            return self.send_text(handler, HTTPStatus.NOT_FOUND, "text/plain", "introuvable")
        self.send_text(handler, HTTPStatus.OK, "text/html", f"<h1>{esc(row['title'])}</h1><p>{esc(row['author'])}</p>")

    def epub(self, handler, book_id: int):
        row = self.cache.get(book_id)
        if row is None:
            return self.send_text(handler, HTTPStatus.NOT_FOUND, "text/plain", "introuvable")
        path = self.cache.epub_path(book_id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            write_epub(path, self.cache.as_book(row))
        self._send(handler, HTTPStatus.OK, "application/epub+zip", path.read_bytes())

    def _inject_fault(self, handler, route: str, fault: str) -> bool:
        """Opt-in HTTP negatives only; never materialize an EPUB or mutate data."""
        if fault == "429":
            self.send_text(handler, HTTPStatus.TOO_MANY_REQUESTS, "text/plain",
                           "synthetic rate limit", headers={"Retry-After": "3"})
            return True
        if fault == "503":
            self.send_text(handler, HTTPStatus.SERVICE_UNAVAILABLE, "text/plain",
                           "synthetic unavailable")
            return True
        if route in {"/", "/ebooks.php", "/details.php"}:
            if fault == "login_html":
                self.send_text(handler, HTTPStatus.OK, "text/html",
                               "<html><body><form id='login'>"
                               "<input name='username'></form></body></html>")
                return True
            if fault == "malformed_html":
                self.send_text(handler, HTTPStatus.OK, "text/html",
                               "<html><body><ul><li>broken fixture")
                return True
        if route == "/newsendbook.php":
            if fault == "expired_link":
                self.send_text(handler, HTTPStatus.GONE, "text/plain", "synthetic expired link")
                return True
            if fault in {"wrong_mime", "missing_format"}:
                self.send_text(handler, HTTPStatus.OK, "text/html",
                               "<html><body>no EPUB available</body></html>")
                return True
            if fault == "invalid_epub":
                self._send(handler, HTTPStatus.OK, "application/epub+zip", b"not a ZIP/EPUB")
                return True
        return False

    def route_get(self, handler):
        parsed = urlsplit(handler.path)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path == "/health":
            return self.send_json(handler, {"ok": True, "books": self.cache.count()})
        if parsed.path in {"/", "/ebooks.php", "/details.php", "/newsendbook.php"}:
            if self._inject_fault(handler, parsed.path, (qs.get("ux_fault") or [""])[0]):
                return
        if parsed.path in {"/", "/ebooks.php"}:
            return self.page(
                handler,
                author_id=(qs.get("id_auteur") or ["0"])[0],
                category_id=(qs.get("id_categorie") or ["1"])[0],
                genre_id=(qs.get("id_genre") or ["1"])[0],
                offset=int((qs.get("begin") or ["0"])[0] or 0),
                limit=int((qs.get("offset") or ["50"])[0] or 50),
            )
        if parsed.path == "/details.php":
            return self.details(handler, int((qs.get("id") or ["0"])[0] or 0))
        if parsed.path == "/newsendbook.php":
            return self.epub(handler, int((qs.get("id") or ["0"])[0] or 0))
        return self.send_text(handler, HTTPStatus.NOT_FOUND, "text/plain", "route inconnue")

    def route_post(self, handler):
        parsed = urlsplit(handler.path)
        if parsed.path != "/ebooks.php":
            return self.send_text(handler, HTTPStatus.NOT_FOUND, "text/plain", "route inconnue")
        qs = parse_qs(parsed.query, keep_blank_values=True)
        if self._inject_fault(handler, parsed.path, (qs.get("ux_fault") or [""])[0]):
            return
        length = int(handler.headers.get("Content-Length") or 0)
        body = handler.rfile.read(min(length, 64 * 1024)).decode("utf-8", errors="replace")
        form = parse_qs(body, keep_blank_values=True)
        return self.page(
            handler,
            title=(form.get("titre") or [""])[0],
            offset=int((form.get("offset") or ["0"])[0] or 0),
            limit=50,
        )


class Handler(BaseHTTPRequestHandler):
    server_version = "CiferecaFakeEbooksGratuits/1.0"
    app: EbooksGratuitsApp

    def do_GET(self):
        try:
            self.app.route_get(self)
        except BrokenPipeError:
            pass

    def do_POST(self):
        try:
            self.app.route_post(self)
        except BrokenPipeError:
            pass

    def log_message(self, fmt: str, *args):
        sys.stderr.write("[http] %s - %s\n" % (self.address_string(), fmt % args))


def parse_args():
    p = argparse.ArgumentParser(description="Pseudo service Ebooks libres et gratuits")
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--cache-dir", type=Path, default=Path(".ebooksgratuits-fake-cache"))
    p.add_argument("--books", type=int, default=DEFAULT_BOOKS)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--build-cache-only", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.books < 1:
        raise SystemExit("--books doit être >= 1")
    cache = DatasetCache(args.cache_dir, args.books, args.seed, args.rebuild)
    if args.build_cache_only:
        print(json.dumps({"books": cache.count()}, ensure_ascii=False))
        return 0
    Handler.app = EbooksGratuitsApp(cache)
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"[server] http://{args.bind}:{args.port}/ebooks.php")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
