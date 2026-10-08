# Cifereca EPUB/PDF Optimizer 0.3.1

Compatible avec **Plugin API >=1.4,<2** (source de vérité : `cifereca-plugin.json`). Le moteur est publié comme `document.transformer` et les profils comme services publics `device.profile`.

## But

Créer une variante EPUB e-ink sans modifier l'original ni dégrader silencieusement la sémantique du livre.

La version 0.3.1 apporte :

- conservation byte-for-byte des images animées ;
- conservation byte-for-byte des images transparentes, sans aplatissement arbitraire ;
- limites configurables par image : `max_image_mb` et `max_image_pixels` ;
- refus des chemins ZIP dangereux ou dupliqués ;
- hints de taille XHTML/CSS inline utilisables seulement si le profil publie explicitement `max_zoom_factor` ;
- orientation paysage utilisable seulement si le profil publie `allow_landscape` ;
- estimation de taille et diagnostic des images candidates dans `artifact.plan` ;
- progression pondérée par le volume des images plutôt que par leur seul nombre.

Quand le zoom est inconnu, un hint de layout ne peut pas réduire une image sous la résolution physique de l'écran.

## Profils intégrés

| Profil | Fit box | Gris | JPEG | Zoom/paysage |
|---|---:|---:|---:|---|
| Xteink X3 / CrossPoint | 528×792 | 4 niveaux + dithering | 70 | non déclaré |
| Xteink X4 / CrossPoint | 480×800 | 4 niveaux + dithering | 70 | non déclaré |
| Kobo Clara BW | 1072×1448 | 8 bits | 82 | non déclaré |
| E-ink générique | 1200×1600 | 8 bits | 80 | non déclaré |

Les capacités de zoom/paysage ne sont pas inventées par le transformer : un profil tiers peut les annoncer via le contrat structurel `cifereca.device.profile/1`.

## Sécurité et confidentialité

- aucun accès réseau ;
- refus conservateur du DRM de contenu ;
- limites ZIP et image configurables ;
- écriture atomique ;
- original jamais modifié.

## Tests

```bash
cd src/plugins/epub_optimizer
python -m pytest -q
```
