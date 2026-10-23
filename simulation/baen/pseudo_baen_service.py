#!/usr/bin/env python3
"""Faux catalogue Baen Free Library (contrats HTTP), uniquement en boucle locale.

Les vraies URL HTTPS Baen du plugin ne sont jamais réécrites en production.
La redirection des requêtes se fait seulement par le client HTTPX injecté
dans les tests, sans DNS ni jeton utilisateur.
"""
from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import html
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.fake_books import FakeBookFactory, epub_bytes
from plugin_scenarios import reproducible_epub

SEED = 20261009
BOOKS = (
    ("firefly-atlas", "The Firefly Atlas", "Camille Example", "2/1/2000", "9780140328721", True),
    ("garden-of-words", "The Garden of Words", "Alex Example", "5/11/2003", "9780306406157", False),
    ("last-shore", "The Last Shore", "Sam Example", "8/14/2010", "9780553386790", True),
)


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


def _query_int(args: dict, key: str, default: int, *, maximum: int = 100) -> int:
    try:
        return max(1, min(maximum, int((args.get(key) or [str(default)])[0])))
    except (ValueError, TypeError):
        raise ValueError("invalid integer query")


class BaenFakeApp:
    def __init__(self, cache_dir: Path, *, seed: int = SEED, rebuild: bool = False) -> None:
        self.cache_dir = Path(cache_dir)
        self.seed = int(seed)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.epub_path = self.cache_dir / "public-free.epub"
        self.seed_path = self.cache_dir / "seed.txt"
        if rebuild or not self.epub_path.is_file() or (
            not self.seed_path.is_file() or self.seed_path.read_text() != str(self.seed)
        ):
            book = FakeBookFactory(self.seed, "ux-baen-public").make(1)
            self.epub_path.write_bytes(reproducible_epub(epub_bytes(book)))
            self.seed_path.write_text(str(self.seed), encoding="ascii")

    @staticmethod
    def send(h: BaseHTTPRequestHandler, status: HTTPStatus, typ: str, body: bytes) -> None:
        h.send_response(int(status))
        h.send_header("Content-Type", typ)
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Cache-Control", "no-store")
        h.send_header("X-Content-Type-Options", "nosniff")
        h.end_headers()
        h.wfile.write(body)

    def json(self, h: BaseHTTPRequestHandler, status: HTTPStatus, data: dict) -> None:
        content = (json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        self.send(h, status, "application/json", content)

    def list_books(self, h: BaseHTTPRequestHandler, args: dict) -> None:
        q = str((args.get("q") or [""])[0]).casefold().strip()
        if q == "rate-limit":
            return self.send(h, HTTPStatus.TOO_MANY_REQUESTS, "text/plain", b"429 simulated")
        if q == "down":
            return self.send(h, HTTPStatus.SERVICE_UNAVAILABLE, "text/plain", b"503 simulated")
        if q == "layout-change":
            # HTTP 200 without attested result/zero markers: a page-layout
            # regression must remain partial, never a confirmed zero.
            return self.send(h, HTTPStatus.OK, "text/html",
                             b"<html><body>Unknown synthetic catalogue layout</body></html>")
        page = _query_int(args, "page", 1)
        per_page = _query_int(args, "pageSize", 50)
        found = [b for b in BOOKS if not q or q in b[1].casefold() or q in b[2].casefold()]
        rows = found[(page - 1) * per_page:page * per_page]
        links = "".join(
            f'<article><a href="/allbooks/{slug}">{_escape(title)}</a></article>'
            for slug, title, _, _, _, _ in rows
        )
        # The production plugin requires BOTH source-visible zero markers.
        # Only a truly empty matching corpus supplies them. An exhausted
        # nonempty page is not the same thing as an empty remote catalogue.
        empty_proof = ("<p>Showing 0-0 of 0</p><p>No results</p>"
                       if not found else "")
        body = (
            "<!doctype html><html><body>"
            "<a href='/allbooks/not-a-record'>More...</a>"
            + empty_proof + links + '<a href="/allbooks/not-a-record">Buy</a>'
            + "</body></html>"
        ).encode("utf-8")
        return self.send(h, HTTPStatus.OK, "text/html; charset=utf-8", body)

    def detail(self, h: BaseHTTPRequestHandler, slug: str) -> None:
        entry = next((b for b in BOOKS if b[0] == slug), None)
        if not entry:
            return self.send(h, HTTPStatus.NOT_FOUND, "text/plain", b"missing")
        _, title, author, published, isbn, downloadable = entry
        download = (
            f'<a href="/downloads/{slug}.epub">Download EPUB</a>'
            if downloadable else "<p>EPUB public indisponible pour ce livre</p>"
        )
        body = (
            f'<!doctype html><html><head><title>{_escape(title)} by '
            f'{_escape(author)} - WebScription Ebook</title></head>'
            f'<body><p>Published: {published}</p><p>ISBN: {isbn}</p>'
            + download + "</body></html>"
        ).encode("utf-8")
        self.send(h, HTTPStatus.OK, "text/html; charset=utf-8", body)

    def route_get(self, h: BaseHTTPRequestHandler) -> None:
        parsed = urlsplit(h.path)
        args = parse_qs(parsed.query)
        if parsed.path == "/health":
            return self.json(h, HTTPStatus.OK, {
                "ok": True, "synthetic": True, "seed": self.seed, "catalog": len(BOOKS),
                "downloadable": sum(bool(b[-1]) for b in BOOKS),
            })
        if parsed.path == "/allbooks/category/index/id/2012":
            return self.list_books(h, args)
        if parsed.path.startswith("/allbooks/"):
            return self.detail(h, parsed.path.removeprefix("/allbooks/").rstrip("/"))
        if parsed.path.startswith("/downloads/") and parsed.path.endswith(".epub"):
            slug = parsed.path.removeprefix("/downloads/").removesuffix(".epub")
            entry = next((b for b in BOOKS if b[0] == slug), None)
            if entry is None or not entry[-1]:
                return self.send(h, HTTPStatus.NOT_FOUND, "text/plain", b"missing")
            if (args.get("fault") or [""])[0] == "invalid-epub":
                return self.send(h, HTTPStatus.OK, "application/epub+zip", b"invalid EPUB")
            return self.send(h, HTTPStatus.OK, "application/epub+zip",
                             self.epub_path.read_bytes())
        return self.json(h, HTTPStatus.NOT_FOUND, {"error": "unknown synthetic route"})


class Handler(BaseHTTPRequestHandler):
    server_version = "CiferecaFakeBaen/1.0"
    app: BaenFakeApp

    def do_GET(self) -> None:
        try:
            self.app.route_get(self)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, TypeError):
            self.app.json(self, HTTPStatus.BAD_REQUEST, {"error": "bad input"})

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("[baen-fake] %s - %s\n" % (self.address_string(), fmt % args))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8100)
    p.add_argument("--cache-dir", type=Path, required=True)
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--rebuild", action="store_true")
    args = p.parse_args(argv)
    if args.bind not in ("127.0.0.1", "localhost"):
        raise SystemExit("Serveur de test : bind loopback uniquement")
    Handler.app = BaenFakeApp(args.cache_dir, seed=args.seed, rebuild=args.rebuild)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[server] synthetic Baen http://127.0.0.1:{server.server_port}/health", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
