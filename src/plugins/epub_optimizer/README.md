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
restent traitées par la voie séquentielle. Depuis 0.3.7, aucun pool
n'est ouvert pour une image seule ; les images séparées par XHTML/CSS
peuvent partager un lot sans changer l'ordre d'écriture. L'admission
est aussi plafonnée à 128 Mio de travail décodé estimé (16 × pixels +
2 × octets source) par lot. Un mono-cœur reste séquentiel avec avertissement.
Attente bornée à 120 s depuis la soumission de chaque image avec annulation vérifiée régulièrement ; une interruption
du pool retire la sortie temporaire et ne modifie pas l'original.

## P1 #2432 — PDF : lots Pillow isolés (0.3.6)

Le transformer `document.transformer.pdf-eink-optimizer` accepte également
`cpu_workers=1|2|4` dans sa configuration ou les options de la requête.
Le plan et le résultat indiquent la valeur demandée. **1 reste la valeur par
défaut**, sans processus supplémentaire. Le coordinateur garde seul le PDF,
les extractions PyMuPDF, `replace_image`, la validation et la publication.
Aucun objet PyMuPDF n'est sérialisé vers les travailleurs.

Les lots contiennent au plus N images, **16 Mio d'octets source** et
**128 Mio de mémoire de travail estimée** (16 × largeur × hauteur + 2 ×
octets source par image). Ce dernier plafond est une règle d'admission,
**pas une limite RSS imposée par le système** : interpréteur, PyMuPDF,
IPC, caches et sorties peuvent ajouter de la mémoire. Le coordinateur peut
retenir une extraction supplémentaire pour déterminer le prochain lot.
Les images dépassant ces budgets et les lots d'une seule image restent
séquentiels ; un document à une image n'ouvre donc aucun pool. Le nombre de
processus est plafonné au nombre de CPU annoncé par l'OS ; les quotas CPU de
conteneur ne sont pas détectés. Sur mono-cœur, un avertissement indique le
repli séquentiel.

Le pool `spawn` est créé au premier lot parallèle, avec renouvellement après
64 images par travailleur. Une erreur de **création avant soumission**
(`OSError`/`RuntimeError`) autorise le repli séquentiel avec avertissement.
Une rupture IPC, un travailleur tué, un `MemoryError` ou un délai dépassé
interrompt l'opération ; aucun retry silencieux. Délai absolu de 120 s par
image depuis sa soumission, avec vérification d'annulation toutes les 200 ms.
Les processus sont terminés à la sortie du contexte. Le PDF temporaire est
supprimé sur échec ; une cible déjà présente est conservée. Une annulation
est aussi vérifiée après validation, immédiatement avant publication.
Les refus des PDF protégés/signés et la conservation des masques/stencils,
des textes et du vectoriel restent applicables.

### Mesures reproductibles (#1741)

Banc explicite hors réseau, sur **documents synthétiques**, sans nouveau
collecteur de diagnostics dans le produit :

```bash
cd src/plugins/epub_optimizer
python tests/benchmark_optimizer_cpu.py --repetitions 20 > /tmp/optimizer-cpu.json
```

Même fixture EPUB/PDF légère (une image 160×220) ou lourde (12 images
1000×1500), modes 1/2/4 alternés, une passe de chauffe exclue, 20 mesures
retenues par mode et scénario. Chaque mesure lance un coordinateur neuf ;
création du pool, transformation, validation, hash et publication sont
inclus dans le temps, création des fixtures et export exclus. Le JSON
conserve les échantillons, p50/p95 (rang le plus proche), dispersion,
CPU coordinateur + enfants, tailles et égalité de contenu 1/2/4.
RSS : pics `getrusage` du coordinateur et du plus gros enfant terminé,
en KiB sous Linux ; **ce n'est pas la somme des RSS simultanées**.
Aucune latence GTK ni installation utilisateur n'est mesurée. La campagne
ne suffit pas à qualifier un gain universel. Pour comparer le mode 1
au même code de référence, ajouter `--baseline-root /chemin/package-reference`
et `--baseline-ref SHA` : le répertoire doit contenir le package
`cifereca_epub_optimizer` extrait de ce SHA. Le banc alterne alors aussi
un mode `baseline` séquentiel et vérifie son égalité de contenu avec 1/2/4.
Les modes 2/4 restent opt-in ; les mesures et limites sont consignées dans
#2432, le suivi transversal reste #1741.

---

# Cifereca EPUB/PDF Optimizer 0.3.7

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


## Finalisation CPU 0.3.7 (#2432)

Le réglage `cpu_workers` de la page s'applique aux fichiers choisis localement ;
les actions de bibliothèque utilisent le réglage de leur transformer.
Défaut 1 partout : les mesures synthétiques montrent un coût de démarrage
qui peut dépasser tout bénéfice du parallélisme. 2/4 sont des options
explicites, pas une accélération annoncée. Le pool EPUB est créé au premier
lot d'au moins deux images admissibles ; XHTML/CSS restent lus au fil de
l'écriture. Les deux moteurs gardent source/cible précédente et suppriment
les temporaires sur crash, annulation ou erreur. Les limites de lot sont
une admission mémoire estimée, pas une garantie RSS du système ;
PyMuPDF et l'interpréteur conservent leurs propres allocations.

Recette GTK reproductible (widget réel `OptimizerPage`, vrai broker Core,
transformations réelles et processus `spawn` 1/2/4 ; données synthétiques,
pas une recette de l'application entière sur un poste utilisateur) :

```bash
# Après préparation de la recette environnements/chatgpt-work/README.md
PYTHONPATH="$PWD/src:$PWD/src/plugins/epub_optimizer" \
  environnements/chatgpt-work/work headless -- \
  environnements/chatgpt-work/.venv/bin/python \
  src/plugins/epub_optimizer/tests/gtk_cpu_responsiveness.py
```

La recette mesure les intervalles d'un heartbeat GLib de 10 ms pendant le
travail, vérifie progression, retour à l'état disponible, intégrité source
et égalité de contenu 1/2/4. Le contexte de profils est synthétique ; le
widget, GTK, les tâches et la transformation ne sont pas substitués.
Pour comparer le seul mode 1 à une version de référence du package,
utiliser `CIFERECA_GTK_BASELINE=1` et
`CIFERECA_GTK_PACKAGE_ROOT=/chemin/package-reference`.
Les résultats chiffrés, échantillons et verdicts sont consignés dans #2432 ;
le suivi performance transversal reste #1741. Aucun tag ni release implicite.
