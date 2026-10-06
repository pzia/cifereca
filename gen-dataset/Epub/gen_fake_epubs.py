#!/usr/bin/env python3
"""
gen_fake_epubs.py — générateur de JDD EPUB fictifs.

Exemples :
    python3 gen_fake_epubs.py -n 10 -o ./jdd_epub
    python3 gen_fake_epubs.py -n 20 -o ./jdd_epub --seed 42

Dépendances : Python 3 uniquement (bibliothèque standard).
Format : EPUB 3 avec NCX EPUB 2 de compatibilité.
"""

from __future__ import annotations

import argparse
import html
import random
import re
import unicodedata
import uuid
import zipfile
from datetime import date, timedelta
from pathlib import Path

FIRST_NAMES = [
    "Alice", "Bastien", "Camille", "Diane", "Élias", "Inès", "Jules", "Lina",
    "Maël", "Nora", "Oscar", "Salomé", "Théo", "Yasmine", "Zoé"
]
LAST_NAMES = [
    "Armand", "Beaulieu", "Caron", "Delmas", "Faure", "Garnier", "Hébert",
    "Lefèvre", "Moreau", "Perrin", "Roche", "Valette", "Vidal"
]
PUBLISHERS = [
    "Éditions du Passage", "La Lucarne", "Atelier Boréal", "Maison Sillage",
    "Les Presses du Rivage", "Éditions des Heures", "Le Papier Bleu"
]
PLACES = [
    "la baie des Brumes", "le quartier des Verrières", "la vallée d'Argile",
    "l'île aux Lanternes", "la gare de Minuit", "les collines de Verre",
    "le port des Écluses", "la forêt de Cendre"
]
OBJECTS = [
    "phare", "carnet", "miroir", "violon", "passage", "jardin", "train",
    "secret", "atelier", "silence"
]
GENRES = ["Roman", "Mystère", "Aventure", "Fantastique", "Fiction contemporaine"]
TITLE_PATTERNS = [
    "Le {object} de {place}",
    "La mémoire de {place}",
    "Les nuits de {place}",
    "Sous le ciel de {place}",
    "Le secret de {place}",
    "Une saison à {place}",
]
PALETTES = [
    ("#183153", "#f4d35e", "#ffffff"),
    ("#3b1f47", "#e07a5f", "#f4f1de"),
    ("#143642", "#ec9a29", "#ffffff"),
    ("#4f2d3f", "#d9b44a", "#f7f3e8"),
    ("#1d3557", "#e63946", "#f1faee"),
    ("#264653", "#e9c46a", "#f4f1de"),
]


def esc(value: str) -> str:
    return html.escape(value, quote=True)


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower() or "livre"


def make_title(rng: random.Random) -> str:
    return rng.choice(TITLE_PATTERNS).format(
        object=rng.choice(OBJECTS),
        place=rng.choice(PLACES),
    )


def make_chapter(title: str, place: str, rng: random.Random) -> str:
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
    discovery = rng.choice([
        "un nom qu'elle n'avait jamais vu",
        "une date impossible",
        "une phrase écrite de sa propre main",
        "un plan marqué d'une croix",
    ])
    paragraphs = [
        f"À {place}, {weather}. Depuis l'aube, personne ne parlait de ce qui s'était passé la veille. Pourtant, au café de la place, chaque regard revenait vers la petite boîte posée derrière le comptoir.",
        "Quand la cloche sonna neuf heures, Camille poussa la porte. Elle était venue pour une seule raison : comprendre pourquoi cette boîte apparaissait dans les souvenirs de sa famille depuis trois générations. Sur le comptoir, une enveloppe portait simplement son prénom.",
        f"Elle l'ouvrit sans s'asseoir. À l'intérieur se trouvait une feuille pliée en quatre. Au centre, elle découvrit {discovery}. Dehors, {sound} sembla soudain beaucoup plus proche.",
        "Camille relut le message. Il ne donnait aucune explication, seulement une instruction : « Avant la tombée du jour, retourne là où tout a commencé. » Elle pensa au vieux chemin derrière la gare, à la maison fermée depuis l'enfance et aux histoires que sa mère refusait de raconter.",
        f"Au dos de la feuille, un second indice reprenait exactement les mots du titre : « {title} ». Elle sentit que cette phrase n'avait pas été choisie au hasard. Quelqu'un savait qu'elle viendrait, et quelqu'un attendait qu'elle poursuive.",
        "Elle glissa la feuille dans sa poche et sortit. Le premier secret venait de s'ouvrir, et Camille comprit que la journée ne suivrait aucun des plans qu'elle avait faits.",
    ]
    return "\n".join(f"<p>{esc(p)}</p>" for p in paragraphs)


def svg_cover(title: str, author: str, colors: tuple[str, str, str]) -> str:
    bg, accent, fg = colors
    words = title.split()
    lines, current = [], []
    for word in words:
        trial = " ".join(current + [word])
        if len(trial) <= 24:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    lines = lines[:4]
    title_nodes = "\n".join(
        f'<text x="600" y="{720 + i*95}" text-anchor="middle" font-size="70" '
        f'font-family="serif" font-weight="700" fill="{fg}">{esc(line)}</text>'
        for i, line in enumerate(lines)
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1600" viewBox="0 0 1200 1600">
  <rect width="1200" height="1600" fill="{bg}"/>
  <circle cx="920" cy="280" r="220" fill="{accent}" opacity="0.92"/>
  <rect x="120" y="180" width="18" height="1180" fill="{accent}"/>
  <text x="600" y="260" text-anchor="middle" font-size="30" font-family="sans-serif" letter-spacing="8" fill="{fg}">ROMAN FICTIF</text>
  {title_nodes}
  <line x1="350" y1="1200" x2="850" y2="1200" stroke="{accent}" stroke-width="5"/>
  <text x="600" y="1300" text-anchor="middle" font-size="42" font-family="sans-serif" fill="{fg}">{esc(author)}</text>
  <text x="600" y="1460" text-anchor="middle" font-size="24" font-family="sans-serif" fill="{fg}" opacity="0.75">JEU DE DONNÉES EPUB</text>
</svg>'''


def write_epub(path: Path, meta: dict, chapter_html: str, cover_svg: str) -> None:
    container_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
'''
    css = '''body{font-family:serif;line-height:1.55;margin:7%;color:#222}
h1{font-size:1.8em;margin-bottom:1.2em}
p{text-align:justify;text-indent:1.2em;margin:.55em 0}
.cover{margin:0;padding:0;text-align:center}
.cover img{max-width:100%;max-height:100vh}
.meta{font-family:sans-serif;color:#666;font-size:.85em;text-indent:0}
'''
    cover_xhtml = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="fr">
<head><title>Couverture</title><link rel="stylesheet" href="style.css"/></head>
<body class="cover"><img src="cover.svg" alt="Couverture"/></body>
</html>
'''
    chapter = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="fr">
<head><title>{esc(meta["title"])}</title><link rel="stylesheet" href="style.css"/></head>
<body>
  <h1>Chapitre 1 — Le signe</h1>
  {chapter_html}
  <p class="meta">Texte fictif généré pour un jeu de données de test.</p>
</body>
</html>
'''
    nav = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="fr">
<head><title>Navigation</title></head>
<body><nav epub:type="toc" id="toc"><h1>Sommaire</h1><ol>
<li><a href="chapter1.xhtml">Chapitre 1 — Le signe</a></li>
</ol></nav></body></html>
'''
    ncx = f'''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head><meta name="dtb:uid" content="{esc(meta["identifier"])}"/></head>
<docTitle><text>{esc(meta["title"])}</text></docTitle>
<navMap><navPoint id="navPoint-1" playOrder="1"><navLabel><text>Chapitre 1 — Le signe</text></navLabel><content src="chapter1.xhtml"/></navPoint></navMap>
</ncx>
'''
    opf = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="fr">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{esc(meta["identifier"])}</dc:identifier>
    <dc:title>{esc(meta["title"])}</dc:title>
    <dc:creator>{esc(meta["author"])}</dc:creator>
    <dc:publisher>{esc(meta["publisher"])}</dc:publisher>
    <dc:language>fr</dc:language>
    <dc:date>{esc(meta["date"])}</dc:date>
    <dc:subject>{esc(meta["genre"])}</dc:subject>
    <dc:description>{esc(meta["description"])}</dc:description>
    <meta property="dcterms:modified">{esc(meta["modified"])}</meta>
    <meta name="cover" content="cover-image"/>
  </metadata>
  <manifest>
    <item id="cover-image" href="cover.svg" media-type="image/svg+xml" properties="cover-image"/>
    <item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>
    <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="style.css" media-type="text/css"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="cover-page" linear="no"/><itemref idref="chapter1"/></spine>
</package>
'''

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        # EPUB : mimetype doit être le premier fichier et ne pas être compressé.
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, data in [
            ("META-INF/container.xml", container_xml),
            ("OEBPS/content.opf", opf),
            ("OEBPS/style.css", css),
            ("OEBPS/cover.svg", cover_svg),
            ("OEBPS/cover.xhtml", cover_xhtml),
            ("OEBPS/chapter1.xhtml", chapter),
            ("OEBPS/nav.xhtml", nav),
            ("OEBPS/toc.ncx", ncx),
        ]:
            z.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)


def generate(count: int, output_dir: Path, seed: int | None) -> list[Path]:
    rng = random.Random(seed)
    base_date = date(2012, 1, 1)
    created, used_titles = [], set()

    for index in range(1, count + 1):
        title = make_title(rng)
        while title in used_titles:
            title = make_title(rng)
        used_titles.add(title)

        author = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        publisher = rng.choice(PUBLISHERS)
        genre = rng.choice(GENRES)
        place = rng.choice(PLACES)
        pub_date = base_date + timedelta(days=rng.randrange(0, 5000))
        identifier = f"urn:uuid:{uuid.UUID(int=rng.getrandbits(128))}"

        meta = {
            "title": title,
            "author": author,
            "publisher": publisher,
            "genre": genre,
            "date": pub_date.isoformat(),
            "identifier": identifier,
            "modified": f"{pub_date.isoformat()}T12:00:00Z",
            "description": f"Un {genre.lower()} fictif autour de {place}, créé comme donnée de test EPUB.",
        }
        chapter_html = make_chapter(title, place, rng)
        cover = svg_cover(title, author, rng.choice(PALETTES))
        path = output_dir / f"{index:03d}-{slugify(title)}.epub"
        write_epub(path, meta, chapter_html, cover)
        created.append(path)
        print(f"{index:03d}  {title} — {author} -> {path}")

    return created


def main() -> int:
    parser = argparse.ArgumentParser(description="Génère un jeu de données d'EPUB fictifs.")
    parser.add_argument("-n", "--count", type=int, default=10, help="Nombre d'EPUB à créer (défaut: 10)")
    parser.add_argument("-o", "--output", type=Path, default=Path("./jdd_epub"), help="Répertoire de sortie")
    parser.add_argument("--seed", type=int, default=None, help="Graine aléatoire pour un JDD reproductible")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count doit être >= 1")
    args.output.mkdir(parents=True, exist_ok=True)
    created = generate(args.count, args.output, args.seed)
    print(f"\n{len(created)} EPUB généré(s) dans {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
