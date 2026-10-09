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


## Durcissement ZIP EPUB — 0.3.2

La validation de l’archive EPUB est maintenant effectuée **avant** toute lecture de contenu significative :

- limite globale décompressée contrôlée avant lecture de `mimetype` ;
- chemins ZIP obligatoirement canoniques : refus de `./`, doubles séparateurs et autres alias ;
- doublons insensibles à la casse toujours refusés ;
- entrées ZIP chiffrées refusées explicitement ;
- liens symboliques Unix dans l’archive refusés ;
- entrée `mimetype` obligatoire, non répertoire, bornée à 128 octets et vérifiée après les limites structurelles.

Cette validation est partagée par `analyze_epub()` et `optimize_epub()`. Les règles DRM EPUB, limites d’images, conservation des contenus sensibles et comportement PDF restent inchangés.


## Durcissement OCF EPUB 0.3.3

La validation du conteneur EPUB applique désormais le contrat OCF avant analyse ou transformation :

- `mimetype` doit être **exactement** la première entrée ZIP ;
- nom exact `mimetype`, sans variante de casse ;
- entrée `mimetype` obligatoirement `ZIP_STORED` ;
- aucun `extra field` sur cette entrée ;
- contenu exactement `application/epub+zip`, sans espace, retour ligne ni réparation par `strip()` ;
- les autres entrées ZIP doivent utiliser uniquement Stored ou Deflate.

Ainsi, le plan et la transformation rejettent le même EPUB non conforme au lieu que l’analyse l’accepte puis que la production échoue plus tard. Le pipeline PDF n’est pas modifié.
