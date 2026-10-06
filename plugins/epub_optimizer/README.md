# Cifereca EPUB Optimizer 0.2.1

Plugin Python pour **Cifereca Core 0.9.37+dev.2 / Plugin API 1.x**. Le moteur est publié comme `document.transformer` et les profils comme quatre services `device.profile` indépendants.

## But

Créer une variante EPUB nettement plus légère pour une liseuse e-ink sans modifier la sémantique du livre. La V1 optimise **uniquement les images raster** :

- redimensionnement sans agrandissement ;
- niveaux de gris ;
- quantification + tramage pour Xteink X3/X4 ;
- recompression ;
- conservation exacte des chemins d'images ;
- conservation byte-for-byte des fichiers XHTML/CSS/OPF/NCX/polices/métadonnées ;
- reconstruction EPUB avec `mimetype` en première entrée non compressée ;
- détection conservative du DRM et refus du traitement ;
- écriture atomique : l'original n'est jamais modifié.

Le traitement d'image reprend les choix publics d'**epubkit** (profils X3/X4, 4 niveaux 0/85/170/255, Floyd–Steinberg, autocontraste), mais cette livraison n'embarque pas le serveur FastAPI d'epubkit et n'en copie pas le pipeline destructif complet. Le moteur du plugin est autonome, réduit à Pillow, et conçu pour l'API plugin Cifereca.

## Profils

| Profil | Fit box | Gris | JPEG | Usage |
|---|---:|---:|---:|---|
| Xteink X3 / CrossPoint | 528×792 | 4 niveaux + dithering | 70 | X3 sous firmware rendant les images EPUB |
| Xteink X4 / CrossPoint | 480×800 | 4 niveaux + dithering | 70 | X4 sous firmware rendant les images EPUB |
| **Kobo Clara BW** | **1072×1448** | **8 bits** | **82** | Clara BW actuelle, écran 1448×1072/300 ppp |
| E-ink générique | 1200×1600 | 8 bits | 80 | fallback prudent |

Pour la Clara BW, le plugin ne quantifie pas en 4 gris : l'écran 300 ppp et le pipeline Kobo bénéficient de dégradés 8 bits, tout en supprimant la couleur inutile dans les JPEG.

## Installation

Installer le package via le gestionnaire de plugins Cifereca. Le manifeste déclare `Pillow>=11,<13` comme `python-package` avec sonde `PIL`. Core037-dev.2 installe/résout cette distribution dans **son propre venv avant l'import du transformer**. Le plugin n'exécute jamais pip.

Le package contient une wheel Pillow 12.3.0 pour CPython 3.13 Linux x86_64 manylinux 2.27/2.28 afin de couvrir le mode offline de la plateforme de release observée. D'autres plateformes nécessitent leurs propres wheels qualifiées.

Les quatre profils `device.profile` sont importables sans Pillow ; seul le moteur de transformation charge Pillow paresseusement lors de l'opération.

## Utilisation

1. Ouvrir la page **Optimiser EPUB**.
2. Choisir l'EPUB source.
3. Sélectionner le profil de liseuse.
4. Vérifier le chemin de sortie proposé (`Titre [X3].epub`, `Titre [Clara BW].epub`, etc.).
5. Cliquer **Créer la variante optimisée**.

Le rapport affiche le nombre d'images traitées et la réduction obtenue.

## CLI de test hors Cifereca

Depuis le répertoire décompressé :

```bash
python -m cifereca_epub_optimizer.cli livre.epub --profile kobo-clara-bw
python -m cifereca_epub_optimizer.cli livre.epub --profile xteink-x3 --json
```

## Tests

```bash
python -m unittest discover -s tests -v
```

Les tests vérifient notamment : resize Clara BW, 4 gris X3, intégrité des fichiers non image, packaging EPUB et refus d'un chiffrement ADEPT.

## Intégration Core037

Le transformer reçoit les leases d'Artifact publics du Core et préserve l'original. Les profils liseuse sont découverts via la catégorie `device.profile`, ce qui permet d'ajouter un profil tiers sans modifier le moteur. La page manuelle reste une surface de recette ; elle n'est plus l'unique mode d'intégration.

## Sécurité / confidentialité

- Aucun accès réseau.
- Aucun envoi de fichier.
- Aucun log du contenu EPUB.
- Les erreurs d'image sont rapportées par chemin interne seulement.
- Le traitement s'arrête sur DRM de contenu détecté ; l'obfuscation standard des polices n'est pas considérée comme DRM de contenu.
- Limite anti ZIP-bomb configurable, 2 Gio décompressés par défaut.

## Provenance technique

Le comportement des profils Xteink suit la documentation publique d'epubkit (`b1rdmania/epubkit`, MIT). Aucun code serveur FastAPI n'est nécessaire. La Clara BW est ajoutée par Cifereca avec un profil distinct 1072×1448, gris 8 bits, JPEG qualité 82.
