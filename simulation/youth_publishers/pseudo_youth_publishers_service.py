#!/usr/bin/env python3
"""Éditeurs jeunesse fictifs : Usborne et l'école des loisirs, localhost uniquement.

Les vrais plugins restent verrouillés sur leurs propres domaines HTTPS.
Les tests injectent un transport httpx.MockTransport sans DNS ni comptes.
"""
from __future__ import annotations
import argparse
import html
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

PORT = 8099
SEED = 20261009
US = [
    ("award-book-9781805075103", "Why School Matters", "Age: 3+"),
    ("baby-book-9780140328721", "Baby Book", "Age: 18+ months"),
    ("older-book-9780306406157", "Older Book", "Age: 10+"),
]
FR = [
    ("histoire-petit-loup", "L'histoire de Petit Loup", "3 à 6 ans", "Sylvie Neeman", "album", "9782211348171"),
    ("roman-ado", "Roman ado", "13 ans et +", "Alice Auteur, Bob Auteur", "roman", "9782211345613"),
    ("sans-age", "Sans âge", "indéterminé", "Auteur inconnu", "album", "9782211324649"),
]


def usborne_html(*, page: int) -> bytes:
    rows = US[:2] if page == 1 else US[2:] if page == 2 else []
    parts = ['<!doctype html><html><body><main>']
    for slug, title, age in rows:
        link = "/us/" + slug
        if slug.startswith("award"):
            parts.append(f'<a href="{link}">Award Winner</a>')
        parts.append(f'<a href="{link}">{html.escape(title)}</a><div>{age}</div>')
    if page == 1:
        parts.append('<a href="?p=2">Next Page</a>')
    parts.append("</main></body></html>")
    return "".join(parts).encode("utf-8")


def ecole_html(*, page: int, query: str) -> bytes:
    rows = FR[:2] if page == 1 else FR[2:] if page == 2 else []
    if query.casefold() in {"nothing", "aucun"}:
        rows = []
    parts = ['<!doctype html><html lang="fr"><body>']
    for slug, title, age, author, typ, isbn in rows:
        parts.append(f'<div>{age}</div><a href="/livre/{slug}">{html.escape(title)}</a>'
                     f'<p>{html.escape(author)}</p><div>{typ}</div>'
                     f'<p>ISBN: {isbn}</p>')
    if page == 1 and rows:
        href = ("/recherche-avancee?page=2&search_api_views_fulltext="
                + quote(query) + "&types=livre")
        parts.append(f'<a href="{html.escape(href, quote=True)}">Afficher plus de résultats</a>')
    parts.append("</body></html>")
    return "".join(parts).encode("utf-8")


class FakePublishers:
    def __init__(self, seed: int = SEED):
        self.seed = int(seed)

    @staticmethod
    def send(h, status: int, mimetype: str, body: bytes):
        h.send_response(status)
        h.send_header("Content-Type", mimetype)
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Cache-Control", "no-store")
        h.send_header("X-Content-Type-Options", "nosniff")
        h.end_headers()
        h.wfile.write(body)

    def json(self, h, status: int, data):
        self.send(h, status, "application/json",
                  (json.dumps(data, sort_keys=True, ensure_ascii=False)+"\n").encode("utf-8"))

    def route_get(self, h):
        parsed = urlsplit(h.path)
        qs = parse_qs(parsed.query)
        path = parsed.path
        if path == "/health":
            return self.json(h, 200, {"ok": True, "synthetic": True, "seed": self.seed,
                                      "publishers": ["usborne", "ecole-loisirs"]})
        if path.startswith("/publishers/"):
            fault = (qs.get("fault") or [""])[0]
            if fault == "503":
                return self.send(h, 503, "text/html", b"Unavailable")
            if fault == "wrong-type":
                return self.json(h, 200, {"error": "not HTML"})
            if fault == "oversized":
                return self.send(h, 200, "text/html", b"x" * 2_000_001)
        if path == "/publishers/usborne/us/books/browse-by-age" or path.startswith(
                "/publishers/usborne/us/books/browse-by-age/"):
            page = max(1, min(100, int((qs.get("p") or ["1"])[0])))
            return self.send(h, 200, "text/html; charset=utf-8", usborne_html(page=page))
        if path == "/publishers/ecole/recherche-avancee":
            if (qs.get("types") or ["livre"])[0] != "livre":
                return self.send(h, 200, "text/html; charset=utf-8", ecole_html(page=3, query=""))
            page = max(1, min(100, int((qs.get("page") or ["1"])[0])))
            query = (qs.get("search_api_views_fulltext") or [""])[0]
            return self.send(h, 200, "text/html; charset=utf-8",
                             ecole_html(page=page, query=query))
        return self.json(h, 404, {"error": "unknown synthetic endpoint"})


class Handler(BaseHTTPRequestHandler):
    server_version = "CiferecaFakeYouthPublishers/1.0"
    app: FakePublishers

    def do_GET(self):
        try:
            self.app.route_get(self)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, TypeError):
            self.app.json(self, 400, {"error": "invalid request"})

    def log_message(self, fmt, *args):
        sys.stderr.write("[publisher-fake] %s - %s\n" % (self.address_string(), fmt % args))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--cache-dir", default="")
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--rebuild", action="store_true")
    args = p.parse_args(argv)
    if args.bind not in ("127.0.0.1", "localhost"):
        raise SystemExit("localhost uniquement")
    Handler.app = FakePublishers(args.seed)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[server] youth publishers http://127.0.0.1:{server.server_port}/health",
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
