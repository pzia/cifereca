#!/usr/bin/env python3
"""Catalogue jeunesse Milan synthétique, pour tests de contrats hors ligne.

Les véritables plugins ne voient que leurs URLs officielles ; un transport
HTTPX injecté dans les *tests* redirige les requêtes vers localhost.
"""
from __future__ import annotations

import argparse
import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

DEFAULT_PORT = 8101
SEED = 20261009
# Fixtures inventées ; les ISBN sont des identifiants de forme valide.
PRODUCTS = {
    "63047-moqueuse": {
        "title": "Moqueuse", "age": "À partir de 8 ans",
        "author": "Nathalie Somers", "collection": "Premiers romans",
        "themes": "amitié, entraide", "isbn": "978-2-408-02080-4",
    },
    "77561-lescargot": {
        "title": "L'escargot", "age": "À partir de 1 ans",
        "author": "Illustratrice Exemple", "collection": "Mes animaux",
        "themes": "animaux, éveil", "isbn": "978-2-408-04507-4",
    },
    "99999-adulte": {
        "title": "Essai adulte", "age": "À partir de 18 ans",
        "author": "Auteur Exemple", "collection": "Essais",
        "themes": "essai", "isbn": "978-2-408-02080-4",
    },
    "99998-mauvais-isbn": {
        "title": "Sans ISBN valide", "age": "À partir de 5 ans",
        "author": "Autrice Exemple", "collection": "Découverte",
        "themes": "lecture", "isbn": "9780000000000",
    },
    "88888-sans-age": {
        "title": "Sans âge attesté", "age": "",
        "author": "Auteur Exemple", "collection": "Premiers romans 8-12 ans",
        "themes": "découverte", "isbn": "978-2-408-02080-4",
    },
}
PAGES = {
    1: ("63047-moqueuse", "77561-lescargot", "99999-adulte", "99998-mauvais-isbn"),
    2: ("88888-sans-age",),
}


def listing(page: int) -> bytes:
    items = PAGES.get(page, ())
    body = ["<!doctype html><html lang='fr'><body><main>"]
    for slug in items:
        title = html.escape(PRODUCTS[slug]["title"], quote=True)
        body.append(f'<article><a href="/livres/{slug}/">{title}</a>')
        body.append(f'<a href="/livres/{slug}/">Lire la suite</a></article>')
    if page == 1:
        body.append('<a href="/livres/page/2/">2</a>')
    body.append('<a href="https://example.invalid/livres/77-ignored/">externe</a>')
    body.append("</main></body></html>")
    return "".join(body).encode("utf-8")


def detail(slug: str) -> bytes | None:
    row = PRODUCTS.get(slug)
    if row is None:
        return None
    q = lambda key: html.escape(row[key], quote=True)
    age = f'<div>Âge</div><div>{q("age")}</div>' if row["age"] else ""
    return (
        "<!doctype html><html lang='fr'><body>"
        f'<h1>{q("title")}</h1>'
        f'<div>Auteur</div><div>{q("author")}</div>'
        f'<div>Collection</div><div>{q("collection")}</div>'
        f'<div>Thèmes</div><div>{q("themes")}</div>'
        + age + f'<div>Code ISBN</div><div>{q("isbn")}</div>'
        "</body></html>"
    ).encode("utf-8")


class MilanFakeApp:
    def __init__(self, seed: int = SEED):
        self.seed = int(seed)

    @staticmethod
    def send(handler, status: int, typ: str, payload: bytes):
        handler.send_response(status)
        handler.send_header("Content-Type", typ)
        handler.send_header("Content-Length", str(len(payload)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        handler.wfile.write(payload)

    def json(self, handler, status: int, value: object):
        self.send(handler, status, "application/json; charset=utf-8",
                  (json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))

    def route_get(self, handler):
        parsed = urlsplit(handler.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        if path == "/health":
            return self.json(handler, 200, {"ok": True, "synthetic": True,
                                            "seed": self.seed, "products": len(PRODUCTS)})
        if path.startswith("/milan/"):
            fault = (params.get("fault") or [""])[0]
            if fault == "unavailable":
                return self.send(handler, 503, "text/html", b"Simulated unavailable")
            if fault == "wrong-type":
                return self.json(handler, 200, {"error": "not HTML"})
            if fault == "oversized":
                return self.send(handler, 200, "text/html", b"x" * 2_000_001)
        if path in {"/milan/livres", "/milan/livres/"}:
            return self.send(handler, 200, "text/html; charset=utf-8", listing(1))
        if path.startswith("/milan/livres/page/"):
            page = path.removeprefix("/milan/livres/page/").strip("/")
            if not page.isdigit() or not 1 <= int(page) <= 100:
                return self.json(handler, 404, {"error": "invalid page"})
            return self.send(handler, 200, "text/html; charset=utf-8", listing(int(page)))
        if path.startswith("/milan/livres/"):
            slug = path.removeprefix("/milan/livres/").strip("/")
            rendered = detail(slug)
            if rendered is None:
                return self.json(handler, 404, {"error": "unknown product"})
            return self.send(handler, 200, "text/html; charset=utf-8", rendered)
        return self.json(handler, 404, {"error": "unknown fake endpoint"})


class Handler(BaseHTTPRequestHandler):
    server_version = "CiferecaFakeMilan/1.0"
    app: MilanFakeApp

    def do_GET(self):
        try:
            self.app.route_get(self)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):
        import sys
        sys.stderr.write("[milan-fake] %s - %s\n" % (self.address_string(), fmt % args))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--cache-dir", default="", help="Unused: synthetic fixtures are in-memory")
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--rebuild", action="store_true", help="No-op: no cache")
    a = p.parse_args(argv)
    if a.bind not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Le serveur de test ne peut écouter que localhost")
    Handler.app = MilanFakeApp(seed=a.seed)
    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"[server] synthetic Milan http://127.0.0.1:{server.server_port}/health",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
