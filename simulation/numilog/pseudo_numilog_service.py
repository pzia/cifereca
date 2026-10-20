#!/usr/bin/env python3
"""Pseudo service Numilog pour Cifereca.

JDD par défaut : 1 000 livres possédés. La bibliothèque HTML est paginée,
les couvertures PNG et EPUB sont générés à la demande et mis en cache.

Le serveur reproduit les routes consommées par provider.numilog :
- GET  /
- POST /Login.Aspx/AuthUser
- GET  /Pages/Compte/MesLivres.aspx?page=N
- GET  /Pages/Compte/readBook.ashx?lid=...&m=download
- GET  /Services/Delivery.ashx?lid=...
- GET  /downloads/{lid}.epub\n- GET  /downloads/{lid}.acsm (un livre sur sept)
- GET  /covers/{lid}.png

Configuration Cifereca :
    base_url = http://127.0.0.1:8094
    email     = numilog-test@example.test
    password  = test

Dépendances : Python 3, bibliothèque standard uniquement.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

DATASET_ROOT = Path(__file__).resolve().parents[1]
if str(DATASET_ROOT) not in sys.path:
    sys.path.insert(0, str(DATASET_ROOT))

from common.fake_books import FakeBook, FakeBookFactory, cover_png_bytes, write_epub

APP_VERSION = "1.1"
CACHE_SCHEMA_VERSION = "1"
DEFAULT_BOOKS = 1_000
DEFAULT_PORT = 8094
DEFAULT_PAGE_SIZE = 50
DEFAULT_USERNAME = "numilog-test@example.test"
DEFAULT_PASSWORD = "test"


def esc(value: object) -> str:
    import html
    return html.escape(str(value), quote=True)


class DatasetCache:
    def __init__(self, cache_dir: Path, books: int, seed: int, rebuild: bool = False) -> None:
        self.cache_dir = cache_dir
        self.books = books
        self.seed = seed
        self.db_path = cache_dir / "numilog.sqlite3"
        self.cover_dir = cache_dir / "covers"
        self.epub_dir = cache_dir / "epub"
        self._local = threading.local()
        if rebuild and cache_dir.exists():
            shutil.rmtree(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.cover_dir.mkdir(parents=True, exist_ok=True)
        self.epub_dir.mkdir(parents=True, exist_ok=True)
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
                lid INTEGER PRIMARY KEY,
                identifier TEXT NOT NULL,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                publisher TEXT NOT NULL,
                publication_date TEXT NOT NULL,
                subject TEXT NOT NULL,
                language TEXT NOT NULL,
                place TEXT NOT NULL,
                object_name TEXT NOT NULL,
                description TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_numilog_title ON books(title);
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

        print(f"[cache] Génération bibliothèque Numilog : {self.books} livres…")
        conn.execute("DELETE FROM books")
        conn.execute("DELETE FROM meta")
        factory = FakeBookFactory(self.seed, "numilog")
        rows = []
        for index in range(1, self.books + 1):
            book = factory.make(index)
            rows.append(
                (
                    index,
                    book.identifier,
                    book.title,
                    book.author,
                    book.publisher,
                    book.publication_date,
                    book.subject,
                    book.language,
                    book.place,
                    book.object_name,
                    book.description,
                )
            )
            if len(rows) >= 1000:
                conn.executemany("INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
                rows.clear()
        if rows:
            conn.executemany("INSERT INTO books VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
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

    def page(self, page: int, page_size: int) -> list[sqlite3.Row]:
        offset = max(0, page - 1) * page_size
        return self.connect().execute(
            "SELECT * FROM books ORDER BY lid LIMIT ? OFFSET ?", (page_size, offset)
        ).fetchall()

    def get(self, lid: int) -> sqlite3.Row | None:
        return self.connect().execute("SELECT * FROM books WHERE lid=?", (lid,)).fetchone()

    @staticmethod
    def as_book(row: sqlite3.Row) -> FakeBook:
        return FakeBook(
            index=int(row["lid"]),
            identifier=str(row["identifier"]),
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

    def cover_path(self, lid: int) -> Path:
        return self.cover_dir / f"{lid:06d}.png"

    def epub_path(self, lid: int) -> Path:
        return self.epub_dir / f"{lid:06d}.epub"


class NumilogFakeApp:
    def __init__(self, cache: DatasetCache, username: str, password: str, page_size: int) -> None:
        self.cache = cache
        self.username = username
        self.password = password
        self.page_size = page_size

    @staticmethod
    def base(handler: BaseHTTPRequestHandler) -> str:
        host = handler.headers.get("Host") or f"127.0.0.1:{handler.server.server_port}"
        proto = handler.headers.get("X-Forwarded-Proto", "http").split(",", 1)[0].strip() or "http"
        return f"{proto}://{host}"

    @staticmethod
    def send_bytes(handler, status: int, content_type: str, body: bytes, headers: dict[str, str] | None = None) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            handler.send_header(key, value)
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)

    def send_json(self, handler, payload: object, status: int = HTTPStatus.OK, headers: dict[str, str] | None = None) -> None:
        self.send_bytes(
            handler,
            status,
            "application/json; charset=utf-8",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers,
        )

    def home(self, handler):
        body = (
            "<!doctype html><html><meta charset='utf-8'><title>Numilog fake</title>"
            "<body><h1>Numilog fictif pour Cifereca</h1></body></html>"
        ).encode("utf-8")
        self.send_bytes(handler, HTTPStatus.OK, "text/html; charset=utf-8", body)

    def health(self, handler):
        self.send_json(handler, {"ok": True, "books": self.cache.count(), "page_size": self.page_size})

    def login(self, handler):
        body = handler.json_body or {}
        ok = str(body.get("frmLogin") or "") == self.username and str(body.get("frmPassword") or "") == self.password
        result = {"AuthSuccess": ok, "Message": "" if ok else "Identifiants invalides"}
        headers = {"Set-Cookie": "NUMILOG_FAKE=1; Path=/; HttpOnly; SameSite=Lax"} if ok else None
        self.send_json(handler, {"d": json.dumps(result, ensure_ascii=False)}, HTTPStatus.OK, headers)

    def library(self, handler, qs):
        try:
            page = max(1, int((qs.get("page") or ["1"])[0]))
        except ValueError:
            page = 1
        rows = self.cache.page(page, self.page_size)
        cards = []
        for row in rows:
            lid = int(row["lid"])
            cards.append(
                f"""<div class="book" data-lid="{lid}">
<img class="imgCouv" src="/covers/{lid}.png" alt="Couverture"/>
<span class="pTitle">{esc(row['title'])}</span>
<a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid={lid}&amp;m=download&amp;format=epub">Télécharger</a>
</div>"""
            )
        last = (page * self.page_size) >= self.cache.count()
        next_link = "" if last else f'<a rel="next" href="/Pages/Compte/MesLivres.aspx?page={page + 1}">Suivant</a>'
        body = (
            "<!doctype html><html><meta charset='utf-8'><title>Mes livres</title><body>"
            "<table id='myTable'>"
            + "".join(cards)
            + "</table>"
            + next_link
            + "</body></html>"
        ).encode("utf-8")
        self.send_bytes(handler, HTTPStatus.OK, "text/html; charset=utf-8", body)

    def read_book(self, handler, qs):
        try:
            lid = int((qs.get("lid") or ["0"])[0])
        except ValueError:
            lid = 0
        row = self.cache.get(lid)
        if row is None:
            return self.send_bytes(handler, HTTPStatus.NOT_FOUND, "text/plain", b"unknown book")

        # Cas volontairement atypiques pour les tests de robustesse.
        if lid % 50 == 0:
            return self.send_bytes(handler, HTTPStatus.GONE, "text/plain; charset=utf-8", b"expired fake link")
        if lid % 10 in {1, 2}:
            delivery = f"{self.base(handler)}/Services/Delivery.ashx?lid={lid}"
            body = (
                "<!doctype html><html><body>"
                f'<a id="delivery" href="{esc(delivery)}">Télécharger le fichier</a>'
                "</body></html>"
            ).encode("utf-8")
            return self.send_bytes(handler, HTTPStatus.OK, "text/html; charset=utf-8", body)

        return self.send_bytes(
            handler,
            HTTPStatus.FOUND,
            "text/plain",
            b"",
            {"Location": f"/Services/Delivery.ashx?lid={lid}"},
        )

    def delivery(self, handler, qs):
        try:
            lid = int((qs.get("lid") or ["0"])[0])
        except ValueError:
            lid = 0
        if self.cache.get(lid) is None:
            return self.send_bytes(handler, HTTPStatus.NOT_FOUND, "text/plain", b"unknown book")
        suffix = "acsm" if lid % 7 == 0 else "epub"
        return self.send_bytes(
            handler,
            HTTPStatus.FOUND,
            "text/plain",
            b"",
            {"Location": f"/downloads/{lid}.{suffix}"},
        )

    def cover(self, handler, lid: int):
        row = self.cache.get(lid)
        if row is None:
            return self.send_bytes(handler, HTTPStatus.NOT_FOUND, "text/plain", b"unknown book")
        path = self.cache.cover_path(lid)
        if not path.exists():
            path.write_bytes(cover_png_bytes(self.cache.as_book(row)))
        self.send_bytes(handler, HTTPStatus.OK, "image/png", path.read_bytes())

    def epub(self, handler, lid: int):
        row = self.cache.get(lid)
        if row is None:
            return self.send_bytes(handler, HTTPStatus.NOT_FOUND, "text/plain", b"unknown book")
        path = self.cache.epub_path(lid)
        if not path.exists():
            write_epub(path, self.cache.as_book(row))
        self.send_bytes(handler, HTTPStatus.OK, "application/epub+zip", path.read_bytes())

    def acsm(self, handler, lid: int):
        row = self.cache.get(lid)
        if row is None:
            return self.send_bytes(handler, HTTPStatus.NOT_FOUND, "text/plain", b"unknown book")
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<fulfillmentToken xmlns="http://ns.adobe.com/adept">'
            '<resourceItemInfo><metadata>'
            '<dc:format xmlns:dc="http://purl.org/dc/elements/1.1/">application/epub+zip</dc:format>'
            '</metadata></resourceItemInfo>'
            '</fulfillmentToken>'
        ).encode("utf-8")
        self.send_bytes(handler, HTTPStatus.OK, "application/vnd.adobe.adept+xml", body)

    def negative_protocol(self, handler, path: str, qs: dict[str, list[str]]) -> bool:
        """Opt-in fixture responses on existing Numilog routes only (#771).

        Keep production-like default paths, credentials and fake download assets
        unchanged. In particular, faults cannot create a purchase or file.
        """
        fault = (qs.get("ux_fault") or [""])[0]
        inventory = path.lower() == "/pages/compte/meslivres.aspx"
        login = path == "/Login.Aspx/AuthUser" and handler.command == "POST"
        delivery = path in {"/Pages/Compte/readBook.ashx", "/Services/Delivery.ashx"}
        asset = __import__("re").fullmatch(r"/downloads/\\d+\\.(epub|acsm)", path)
        if not (inventory or login or delivery or asset):
            return False

        if fault == "429":
            self.send_bytes(handler, HTTPStatus.TOO_MANY_REQUESTS, "text/plain",
                            b"fake rate limit", {"Retry-After": "3"})
            return True
        if fault == "503":
            self.send_bytes(handler, HTTPStatus.SERVICE_UNAVAILABLE, "text/plain",
                            b"fake temporary outage")
            return True
        if login and fault == "auth_rejected":
            self.send_json(handler, {"d": json.dumps({
                "AuthSuccess": False, "Message": "Identifiants invalides"
            })})
            return True
        if inventory and fault == "session_expired":
            self.send_bytes(handler, HTTPStatus.UNAUTHORIZED, "text/html",
                            b"<html><body>Session expiree</body></html>")
            return True
        if inventory and fault == "bad_inventory":
            self.send_bytes(handler, HTTPStatus.OK, "text/html",
                            b"<html><body>Unexpected login screen</body></html>")
            return True
        if inventory and fault == "empty_inventory":
            self.send_bytes(handler, HTTPStatus.OK, "text/html",
                            b"<html><body><table id='myTable'></table></body></html>")
            return True
        if inventory and fault == "broken_next":
            try:
                page = int((qs.get("page") or ["1"])[0])
            except ValueError:
                page = 1
            if page >= 2:
                self.send_bytes(handler, HTTPStatus.SERVICE_UNAVAILABLE, "text/plain",
                                b"fake interrupted pagination")
                return True
            # Preserve the normal first page so it offers a next link.
            return False
        if delivery and fault == "expired_link":
            self.send_bytes(handler, HTTPStatus.GONE, "text/plain", b"fake expired link")
            return True
        if path == "/Services/Delivery.ashx" and fault == "missing_format":
            self.send_bytes(handler, HTTPStatus.OK, "text/html",
                            b"<html><body>Format unavailable</body></html>")
            return True
        if asset and fault == "wrong_mime":
            self.send_bytes(handler, HTTPStatus.OK, "text/html",
                            b"<html><body>Not a book</body></html>")
            return True
        if asset and asset.group(1) == "epub" and fault == "invalid_epub":
            self.send_bytes(handler, HTTPStatus.OK, "application/epub+zip",
                            b"not a zip or EPUB")
            return True
        if asset and asset.group(1) == "acsm" and fault == "invalid_acsm":
            self.send_bytes(handler, HTTPStatus.OK, "application/vnd.adobe.adept+xml",
                            b"<fulfillmentToken")
            return True
        return False

    def route(self, handler: BaseHTTPRequestHandler):
        parsed = urlsplit(handler.path)
        path = parsed.path
        qs = parse_qs(parsed.query, keep_blank_values=True)

        if self.negative_protocol(handler, path, qs):
            return

        if path == "/":
            return self.home(handler)
        if path == "/health":
            return self.health(handler)
        if path == "/Pages/Recherche/ResultatRecherche.aspx" and handler.command in {"GET", "HEAD"}:
            # Simulation-only public catalogue, independent of cookies and
            # purchased inventory. Never exposes private delivery links.
            from numilog.public_catalogue import render_public_search
            status, headers, body = render_public_search(self.cache, qs, self.page_size)
            content_type = "text/html; charset=utf-8" if status == HTTPStatus.OK else "text/plain; charset=utf-8"
            return self.send_bytes(handler, status, content_type, body, headers)
        if path == "/Login.Aspx/AuthUser" and handler.command == "POST":
            return self.login(handler)
        if path.lower() == "/pages/compte/meslivres.aspx":
            return self.library(handler, qs)
        if path == "/Pages/Compte/readBook.ashx":
            return self.read_book(handler, qs)
        if path == "/Services/Delivery.ashx":
            return self.delivery(handler, qs)

        m = __import__("re").fullmatch(r"/covers/(\d+)\.png", path)
        if m:
            return self.cover(handler, int(m.group(1)))
        m = __import__("re").fullmatch(r"/downloads/(\d+)\.epub", path)
        if m:
            return self.epub(handler, int(m.group(1)))
        m = __import__("re").fullmatch(r"/downloads/(\d+)\.acsm", path)
        if m:
            return self.acsm(handler, int(m.group(1)))

        self.send_bytes(handler, HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"route inconnue")


class Handler(BaseHTTPRequestHandler):
    server_version = "CiferecaFakeNumilog/1.0"
    app: NumilogFakeApp
    json_body: dict[str, object] | None = None

    def _prepare_body(self):
        self.json_body = None
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise ValueError("corps HTTP trop volumineux")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return
        ctype = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if ctype == "application/json":
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("objet JSON attendu")
            self.json_body = value

    def _dispatch(self):
        try:
            self._prepare_body()
            self.app.route(self)
        except (ValueError, json.JSONDecodeError) as exc:
            self.app.send_bytes(self, HTTPStatus.BAD_REQUEST, "text/plain; charset=utf-8", str(exc).encode("utf-8"))
        except BrokenPipeError:
            pass
        except Exception as exc:
            print(f"[error] {self.command} {self.path}: {type(exc).__name__}: {exc}", file=sys.stderr)
            try:
                self.app.send_bytes(self, HTTPStatus.INTERNAL_SERVER_ERROR, "text/plain", type(exc).__name__.encode())
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
    p = argparse.ArgumentParser(description="Pseudo service Numilog pour Cifereca")
    p.add_argument("--bind", default="127.0.0.1", help="Adresse d'écoute")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port HTTP (défaut: {DEFAULT_PORT})")
    p.add_argument("--cache-dir", type=Path, default=Path(".numilog-fake-cache"), help="Répertoire du cache")
    p.add_argument("--books", type=int, default=DEFAULT_BOOKS, help="Nombre de livres possédés")
    p.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="Livres par page HTML")
    p.add_argument("--seed", type=int, default=20261006, help="Graine déterministe")
    p.add_argument("--username", default=DEFAULT_USERNAME, help="Email accepté")
    p.add_argument("--password", default=DEFAULT_PASSWORD, help="Mot de passe accepté")
    p.add_argument("--rebuild", action="store_true", help="Reconstruire entièrement le cache")
    p.add_argument("--build-cache-only", action="store_true", help="Générer/valider le cache puis quitter")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.books < 1:
        raise SystemExit("--books doit être >= 1")
    if not (1 <= args.page_size <= 200):
        raise SystemExit("--page-size doit être compris entre 1 et 200")
    if not (1 <= args.port <= 65535):
        raise SystemExit("--port invalide")

    cache = DatasetCache(args.cache_dir, args.books, args.seed, args.rebuild)
    if args.build_cache_only:
        print(json.dumps({"books": cache.count()}, ensure_ascii=False))
        return 0

    Handler.app = NumilogFakeApp(cache, args.username, args.password, args.page_size)
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
