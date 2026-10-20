## P1 #2432 — EPUB : travailleurs CPU bornés (pilote 0.3.5)

Le transformer `document.transformer.epub-optimizer` accepte un
`cpu_workers=1|2|4` depuis la configuration ou les options du plan.
La valeur par défaut **1 reste séquentielle**. Avec 2/4, les processus
`spawn` reçoivent seulement les octets de l'image, les paramètres
du profil e-ink et son indice de mise en page. Aucun objet GTK, PyMuPDF,
ZIP, connexion SQLite, compte ni accès réseau n'entre dans le worker.

Le coordinateur conserve l'ordre des entrées, l'écriture exclusive
du ZIP, la vérification DRM/OCF avant traitement, les vérifications
de sortie et la publication atomique. Un lot traite au plus 16 Mio
d'images source et au plus N images en vol ; les images trop grandes
restent traitées par la voie séquentielle. Attente bornée à 120 s
par image avec annulation vérifiée régulièrement ; une interruption
du pool retire la sortie temporaire et ne modifie pas l'original.

Le pilotage PDF/PyMuPDF reste **séquentiel** à ce stade, en attendant
des benchmarks CPU/RSS et une validation du traitement des masques.
Cette tranche ne démontre pas de gain de performance universel.

---

# Cifereca EPUB/PDF Optimizer 0.3.4

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


## Laboratoire IA — prévisualisation sémantique facultative (#784, 0.3.4)

L'optimisation des images EPUB et PDF existante reste **déterministe et
fonctionne sans IA**. L'option de page `llm_semantic_preview=false` est
**désactivée par défaut** et n'affecte pas `artifact.plan` ni
`artifact.transform`.

Après activation, la section repliée « Laboratoire IA » permet de **coller
volontairement** un extrait de 20 à 1 600 caractères, de choisir
simplification, résumé ou glossaire, de cocher un consentement spécifique
puis de cliquer **Prévisualiser (IA)**. Le clic est nécessaire pour chaque
demande. Aucune requête automatique n'est déclenchée en ouvrant la page,
en consultant un livre, en éditant l'extrait ou en optimisant un document.

Une demande `LLMRequest(max_output_tokens=384)` passe par le service public
`context.service("llm")` et le broker `tasks`, hors thread GTK.
**Seul le texte collé est envoyé**, sans contenu lu automatiquement dans
l'EPUB, chemin de fichier, identité, bibliothèque entière ou ZIP. Le
modèle peut être distant : le consentement le signale. La réponse est
bornée, marquée **générée, non vérifiée**, avec fournisseur, modèle et
emplacement local/distant. Les erreurs, absences de service et retours
tardifs après édition sont tolérés sans modification d'artefact.

Cette version fournit uniquement une **prévisualisation**, **pas**
l'application des transformations LLM à une variante EPUB : aucune
écriture, injection XHTML, manifest OPF, ajout de fichiers ou
métadonnées de version n'est exécutée. Une variante sémantique
réellement générée exigerait prévisualisation et validation du diff,
préservation de l'original, traçabilité par passage, contrat
`artifact.execution` Core et tests EPUB d'intégrité avant activation.
Ce lot reste dépendant du vrai service LLM Core #778.

Tests hors réseau : `tests/test_semantic_preview_p2.py`.
