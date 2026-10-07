"""Fabrique déterministe de livres fictifs, couvertures PNG et EPUB.

Bibliothèque standard uniquement. Les générateurs de JDD utilisent un namespace
distinct afin de partager le même algorithme sans produire exactement le même
catalogue par défaut.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import html
import io
from pathlib import Path
import random
import re
import struct
import unicodedata
import uuid
import zipfile
import zlib

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
    "La route {motif}", "Le livre {motif}", "Ce que racontent {motif}",
    "Là où dorment {motif}", "Quand disparaît le {object}",
    "Carnet de voyage : {place}", "Les archives {qualifier}",
    "La chambre {qualifier}", "Le passage {motif}",
]
PALETTES = [
    ((28, 52, 84), (244, 211, 94)), ((59, 31, 71), (224, 122, 95)),
    ((20, 54, 66), (236, 154, 41)), ((79, 45, 63), (217, 180, 74)),
    ((29, 53, 87), (230, 57, 70)), ((38, 70, 83), (233, 196, 106)),
    ((41, 50, 65), (238, 108, 77)), ((43, 45, 66), (239, 35, 60)),
]


@dataclass(frozen=True)
class FakeBook:
    index: int
    identifier: str
    title: str
    author: str
    publisher: str
    publication_date: str
    subject: str
    language: str
    place: str
    object_name: str
    description: str


def stable_int(*parts: object) -> int:
    digest = hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower() or "book"


class FakeBookFactory:
    """Séquence reproductible de livres fictifs.

    Le namespace isole les catalogues : même seed + namespace => même JDD.
    """

    def __init__(self, seed: int = 20261006, namespace: str = "default") -> None:
        self.seed = int(seed)
        self.namespace = str(namespace)
        self.rng = random.Random(stable_int(self.seed, self.namespace))
        self.used_titles: set[str] = set()

    def _title(self, index: int) -> tuple[str, str, str]:
        for _ in range(300):
            place = self.rng.choice(PLACES)
            obj = self.rng.choice(OBJECTS)
            title = self.rng.choice(TITLE_PATTERNS).format(
                object=obj,
                place=place,
                qualifier=self.rng.choice(QUALIFIERS),
                moment=self.rng.choice(MOMENTS),
                motif=self.rng.choice(MOTIFS),
            )
            if title not in self.used_titles:
                self.used_titles.add(title)
                return title, place, obj
        title = f"Chronique fictive — {self.namespace} — {index:06d}"
        self.used_titles.add(title)
        return title, self.rng.choice(PLACES), self.rng.choice(OBJECTS)

    def make(self, index: int) -> FakeBook:
        if index < 1:
            raise ValueError("index doit être >= 1")
        title, place, obj = self._title(index)
        author = f"{self.rng.choice(FIRST_NAMES)} {self.rng.choice(LAST_NAMES)}"
        publisher = self.rng.choice(PUBLISHERS)
        subject = self.rng.choice(SUBJECTS)
        language = self.rng.choice(LANGUAGES)
        start = date(1990, 1, 1)
        pub = start + timedelta(days=self.rng.randrange((date(2026, 10, 1) - start).days))
        identifier = f"urn:uuid:{uuid.UUID(int=stable_int(self.seed, self.namespace, 'uuid', index) << 64 | stable_int('tail', self.seed, self.namespace, index))}"
        description = (
            f"Livre fictif de test. « {title} » se déroule autour de {place} et "
            f"mêle {subject.lower()}, découverte et mémoire. Cette notice est "
            "générée uniquement pour les tests fonctionnels de Cifereca."
        )
        return FakeBook(
            index=index,
            identifier=identifier,
            title=title,
            author=author,
            publisher=publisher,
            publication_date=pub.isoformat(),
            subject=subject,
            language=language,
            place=place,
            object_name=obj,
            description=description,
        )


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def cover_png_bytes(book: FakeBook, width: int = 480, height: int = 720) -> bytes:
    """Couverture raster légère, sans dépendance de police/Pillow."""
    bg, accent = PALETTES[stable_int(book.identifier, "cover") % len(PALETTES)]
    raw = bytearray()
    cx, cy, radius = width * 3 // 4, height // 5, max(18, width // 6)
    for y in range(height):
        raw.append(0)
        for x in range(width):
            circle = (x - cx) ** 2 + (y - cy) ** 2 < radius ** 2
            spine = width // 10 < x < width // 10 + max(5, width // 40)
            divider = height * 2 // 3 < y < height * 2 // 3 + max(3, height // 150)
            raw.extend(accent if circle or spine or divider else bg)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )


def chapter_html(book: FakeBook) -> str:
    rng = random.Random(stable_int(book.identifier, "chapter"))
    weather = rng.choice([
        "une pluie fine rayait les vitres",
        "le brouillard descendait lentement sur les toits",
        "un vent salé remontait les rues",
        "le soleil pâle allongeait les ombres",
    ])
    sound = rng.choice([
        "le grincement d'une enseigne",
        "le souffle régulier des vagues",
        "le claquement lointain d'une porte",
        "le sifflement d'un train",
    ])
    paragraphs = [
        f"À {book.place}, {weather}. Depuis l'aube, quelque chose semblait avoir changé.",
        f"Camille trouva un {book.object_name} posé devant sa porte, accompagné d'une enveloppe sans adresse.",
        f"Elle relut les mots « {book.title} ». Au loin, {sound} interrompit le silence.",
        "Elle comprit que l'objet n'était pas arrivé là par hasard et décida de suivre le premier indice avant la tombée du jour.",
        "Ce chapitre est un texte fictif généré automatiquement pour les tests de Cifereca.",
    ]
    return "\n".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)


def epub_bytes(book: FakeBook, *, cover: bytes | None = None) -> bytes:
    cover = cover if cover is not None else cover_png_bytes(book)
    h = lambda s: html.escape(str(s), quote=True)
    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>
<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
</rootfiles></container>"""
    css = "body{font-family:serif;line-height:1.55;margin:7%;color:#222}p{text-align:justify;text-indent:1.2em}.cover{text-align:center;margin:0}.cover img{max-width:100%;max-height:100vh}"
    cover_page = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="fr"><head><title>Couverture</title>
<link rel="stylesheet" href="style.css"/></head><body class="cover"><img src="cover.png" alt="Couverture"/></body></html>"""
    chapter = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="{h(book.language)}"><head><title>{h(book.title)}</title>
<link rel="stylesheet" href="style.css"/></head><body><h1>Chapitre 1 — Le signe</h1>{chapter_html(book)}</body></html>"""
    nav = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="fr">
<head><title>Navigation</title></head><body><nav epub:type="toc"><h1>Sommaire</h1><ol>
<li><a href="chapter1.xhtml">Chapitre 1 — Le signe</a></li></ol></nav></body></html>"""
    ncx = f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head>
<meta name="dtb:uid" content="{h(book.identifier)}"/></head><docTitle><text>{h(book.title)}</text></docTitle>
<navMap><navPoint id="n1" playOrder="1"><navLabel><text>Chapitre 1 — Le signe</text></navLabel>
<content src="chapter1.xhtml"/></navPoint></navMap></ncx>"""
    modified = book.publication_date + "T12:00:00Z"
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{h(book.language)}">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="bookid">{h(book.identifier)}</dc:identifier><dc:title>{h(book.title)}</dc:title>
<dc:creator>{h(book.author)}</dc:creator><dc:publisher>{h(book.publisher)}</dc:publisher>
<dc:language>{h(book.language)}</dc:language><dc:date>{h(book.publication_date)}</dc:date>
<dc:subject>{h(book.subject)}</dc:subject><dc:description>{h(book.description)}</dc:description>
<meta property="dcterms:modified">{h(modified)}</meta><meta name="cover" content="cover-image"/>
</metadata><manifest>
<item id="cover-image" href="cover.png" media-type="image/png" properties="cover-image"/>
<item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>
<item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="css" href="style.css" media-type="text/css"/>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
</manifest><spine toc="ncx"><itemref idref="cover-page" linear="no"/><itemref idref="chapter1"/></spine></package>"""

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, data in [
            ("META-INF/container.xml", container),
            ("OEBPS/content.opf", opf),
            ("OEBPS/style.css", css),
            ("OEBPS/cover.png", cover),
            ("OEBPS/cover.xhtml", cover_page),
            ("OEBPS/chapter1.xhtml", chapter),
            ("OEBPS/nav.xhtml", nav),
            ("OEBPS/toc.ncx", ncx),
        ]:
            z.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    return out.getvalue()


def write_epub(path: Path, book: FakeBook) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(epub_bytes(book))
    return path
