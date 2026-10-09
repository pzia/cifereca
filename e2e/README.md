# Tests E2E GTK

Le runner canonique est :

```bash
python e2e/run.py
```

Il lance la vraie application CIFERECA sous Xvfb, avec HOME/XDG isolés, puis attend
l'apparition d'une surface GTK non vide. L'attente est conditionnelle et bornée ;
les scénarios ne doivent pas utiliser de `sleep` arbitraire pour synchroniser l'IHM.

## Ciblage

```bash
python e2e/run.py --scenario startup
python e2e/run.py --scenario core-navigation
python e2e/run.py --plugin core
```

Chaque scénario déclare son domaine/plugin. Les futurs scénarios provider doivent
utiliser l'identifiant du plugin comme filtre.

## Artefacts

Les artefacts sont écrits dans `e2e-artifacts/<scenario>/` avec des noms stables :

- `screen.png` : capture de succès ou capture de diagnostic lors d'un échec ;
- `screen.json` : rapport structuré de capture ;
- `stdout.log` / `stderr.log` : sortie du scénario, expurgée des secrets connus ;
- `trace.jsonl` : événements horodatés corrélés au nom du scénario.

Le runner écrit aussi `e2e-artifacts/summary.json`. Ce dossier est généré et ne doit pas être versionné.
Les variables d'environnement dont le nom contient `token`, `password`, `secret`,
`authorization` ou `cookie` sont masquées dans les logs, ainsi que les formes
inline usuelles `token=...`, `password:...`, etc.

## Scénario de référence

`startup` valide un parcours minimal mais réel : lancement de CIFERECA avec le
Core seul, maintien du processus et apparition effective d'une surface GTK rendue.

`core-navigation` part du même état isolé, injecte un clic X11 réel sur
`Connecter les sources`, attend explicitement la stabilisation puis exige un
changement visuel significatif avant d'accepter la capture. L'écran Xvfb est
fixé à `1280x1024x24` afin que les coordonnées du scénario restent déterministes.

## Dépendances Linux

Le runner exige `xvfb-run`, GTK4/libadwaita, PyGObject, Pillow, `libXtst` et
les dépendances Python du Core. La CI installe ces dépendances explicitement.

Les scénarios doivent rester déterministes : aucun service réseau réel, aucun
compte utilisateur réel et aucun état réutilisé entre deux exécutions.


## Fixtures reproductibles

Chaque scénario déclare explicitement une fixture versionnée sous `e2e/fixtures/<nom>/`.
Le runner matérialise cette fixture dans un HOME/XDG temporaire neuf à chaque exécution.
Les chemins variables utilisent le jeton `${E2E_LIBRARY}`, remplacé par la bibliothèque temporaire du run.

La fixture `startup` fournit une identité E2E stable, une bibliothèque vide et aucun compte provider réel.


## Recette P0 #660 — filtres Recherche

`python e2e/run.py --scenario search-filter-p0` ouvre la **vraie fenêtre
CIFERECA** sous Xvfb. Deux identités distinctes sont configurées (Camille et
Alex), avec 36 notices connues associées à Camille et quatre associées
uniquement à Alex. Aucun fournisseur n'est interrogé en ligne.

Le script XTEST clique et saisit **Camille**, lance la recherche locale, puis
ne démarre les captures que lorsque les 36 résultats, le compteur et une
sélection/position de scroll non nulles sont observés. Il ouvre et ferme
physiquement Source, Format, Langue, Champ et Type sans changer les options.
Chaque capture de popover et de retour est corrélée à un relevé GTK :
requête, clés de résultat, compteur, sélection, scroll, route et nombre de
requêtes. Tout changement est un **échec**, pas une capture considérée comme
réussie par défaut.

Artefacts : `e2e-artifacts/search-filter-p0/00-before.png`,
`01-source-open.png`/`01-source-close.png` jusqu'à `05-support-close.png`,
`screen.json` (SHA de commit, mesures et empreintes de captures), et traces
du runner. En cas d'échec, conserver ces preuves et ouvrir un correctif
CoreUI ; ce test ne clôt pas à lui seul #660 avant sa recette sur CI.
