# Tests E2E GTK

**Lire d'abord [environnements/E2E-UX.md](../environnements/E2E-UX.md)**
avant d'exécuter ces scénarios : le guide explique l'environnement rootless,
la simulation, les niveaux de preuve et l'exécution autorisée sur
les runners GitHub standard.


Pour les hôtes avec GTK4/PyGObject installés et accessibles au Python du
scénario, le runner est :

```bash
python3 e2e/run.py
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

## Recette P1 #4118 — couvertures BnF en GTK réel, hors ligne

`python3 e2e/run.py --scenario bnf-covers-1366` exécute le vrai
`MainWindow` GTK/XTEST (1366×900) avec le parseur catalogue BnF et le
projecteur `BnfProviderSession` réels, sans authentification ni réseau externe.
Un `httpx.MockTransport` rend deux PNG synthétiques de couleurs distinctes,
rattachés par le parseur à deux ressources exactes ; une troisième notice
est dépourvue de couverture.

Le scénario vérifie les deux images décodées dans la **liste Recherche**,
leur restitution dans le **Détail** des deux livres, un placeholder pour
la notice sans image, puis dans la **liste Mes emprunts** avec trois prêts
synthétiques de sources exactes et dans leur **Détail**. Il exige un
placeholder pour le prêt sans image, **exactement deux GET image simulés**
et le même cache de couvertures entre Recherche, Détail et Emprunts.
Les captures `01` à `07` et le rapport `screen.json` n'incluent
aucun identifiant, chemin, token ou réponse fournisseur privée.
La partie Emprunts utilise une page HTML d'activité **synthétique** :
`BnfProviderSession.connect()` puis `loans()` appellent le **véritable parseur
BnF** et produisent les trois `Loan` liés à la ressource/credential/compte
exacts, ensuite transmis au contrôleur CoreUI GTK. Le client HTTP du
test est entièrement en mémoire : deux lectures locales (`login`, `activity`),
aucun réseau, token, ACSM ou droits nouveaux. Cela prouve le chaînage
parseur → fournisseur → modèle `Loan` → GTK, mais **pas** l'extraction
réelle des deux cartes BnF depuis un compte installé.
La recette ne prouve **ni** disponibilité effective du CDN public
Feedbooks, **ni** état d'un compte BnF installé.

## Scénario de référence

`startup` valide un parcours minimal mais réel : lancement de CIFERECA avec le
Core seul, maintien du processus et apparition effective d'une surface GTK rendue.

`core-navigation` part du même état isolé, injecte un clic X11 réel sur
`Connecter les sources`, attend explicitement la stabilisation puis exige un
changement visuel significatif avant d'accepter la capture. L'écran Xvfb est
fixé à `1280x1024x24` afin que les coordonnées du scénario restent déterministes.

## Attention : runtime Debian rootless

`environnements/openai-chat-debian/gtk-python` utilise les dépendances
isolées sous `runtime/root`. **`e2e/run.py` remplace actuellement `PYTHONPATH` par `src/` dans
l'environnement du probe**. Pour le runtime Debian local rootless,
transmettre `environnements/openai-chat-debian/gtk-python` par
`--python` : ce lanceur restaure `env.sh` au démarrage du sous-processus.
Sur les runners standard `E2E Smoke`, le venv Python
`--system-site-packages` accède directement à GI/GTK4. Le guide
[`environnements/E2E-UX.md`](../environnements/E2E-UX.md)
distingue les deux chemins. Aucun scénario PASS n'est présumé sans rapport
et captures vérifiés.

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

### P1 #942 — compteur réel et données exclues (GTK 1366)

Le même scénario `book-detail-1366` vérifie après le parcours normal
Recherche/Bibliothèque deux jeux de lignes supplémentaires au **point de
rendu final GTK** `DetailPanel._group("Détail", ...)`. Il évite volontairement
la normalisation en amont afin de tester aussi la sécurité du dernier
composant de présentation.

- Cinq valeurs en entrée, dont une URL signée et un jeton : seules trois
  lignes publiques (Alice, Bob, nature) doivent rester visibles. Le compteur
  annoncé doit être exactement `3 champ(s) bibliographiques`.
- Deux valeurs sensibles sans donnée publique : aucune section « Détail »
  vide ne doit être créée.
- Aucun nouvel HTTP ni recherche en ligne provoquée par ces rendus.
  Les champs secrets ne sont jamais recopiés dans le JSON de preuves.

Rapport : `security_detail_mixed_942` et
`security_detail_all_private_942` dans `screen.json`. Captures
`04-library-detail-mixed-safety-gtk.png` et
`05-library-detail-all-private-gtk.png` à 1366 px. Ce contrôle
d'acceptation reste rouge tant que CoreUI n'a pas corrigé le compteur et
la section sans contenu. Aucun scénario/workflow supplémentaire.


## Recettes Jeunesse — archives retirées (#957)

Les anciennes recettes `youth-filter-help-1366`, `youth-milan-1366`,
`youth-editions-1366`, `youth-1366`, `youth-focus-return-1366`
et la commande `--plugin catalog.youth` **ne sont plus exécutables** :
elles ne figurent pas dans `e2e/run.py::SCENARIOS`. Les packages
`src/plugins/youth-catalog/` et `src/plugins/youth-source-milan/`
ont été retirés. Les anciennes procédures et preuves restent consultables
dans l'historique Git de ce document ; **ne pas les utiliser comme recette
courante ni tenter de réinstaller ces plugins**.

La prévention d'une réactivation des anciens fournisseurs Jeunesse demeure
vérifiée par `tests/test_retired_youth_sources_957.py`. Les jeux de données
synthétiques encore utiles au banc `simulation/` sont indépendants des
anciens parcours GTK. La liste des scénarios actuellement exécutables est
la table `SCENARIOS` de `e2e/run.py` ; `--help` expose les arguments.

## Recettes GTK courantes

### MainWindow preparation baseline (#1123)

`mainwindow-baseline-1366` and `mainwindow-baseline-1600` reuse the real
Diagnostics navigation probe with the two-identity local search fixture.
They add XTEST keyboard focus and identity round-trip, then normal window
closure and refusal of new runner work. Async Diagnostics is simulated;
this proves shell responsiveness, not live provider/LLM behavior.
Run each through `e2e/run.py --scenario <name>` on an existing GTK runtime.
No new workflow or network account is required.

### Shared book-detail comparison at 1600 (#1124)

The existing `e2e/book_detail_probe.py` accepts `--width 1366` and `--width 1600`.
Use the matching Xvfb screen (1366x900 or 1600x1000) and the local
`search-filters` fixture through the existing GTK environment. The same
MainWindow probe checks multivalued Search/Library metadata, independent
subject clipboard copy and the explicit typed ISBN action without remote I/O.
It does not certify live account behavior or resolve the pre-existing duplicate
author copy tooltips. Preserve source hashes with before/after reports.

### CoreUI component lifecycle

`book_detail_probe.py --out <directory> --width 1366 --component-contracts`
(or `--width 1600`) extends the loaded Search/Library detail recipe with empty
and long summaries, XTEST expand/collapse and overflow actions, 0/1/N list
reconciliation, stable-key selection and Escape. The real MainWindow identity
transition cancels a controlled pending observer and clears the old GTK detail;
its scheduler is controlled during this last stage. Deferred worker delivery
is separately covered by the deterministic resolver coordination tests.
Synthetic list keys have no business callbacks. This recipe neither acquires
books nor connects to remote accounts. Jeunesse's existing `--library-search`
recipe also accepts 1366/1600 for local-cache navigation through its real plugin.

`search_filter_probe.py --width 1366|1600` uses the same loaded local-index
fixture to check query, rows, count, selected key, scroll and absence of extra
searches when opening/closing filter menus. `--short-component-actions` keeps
the pre-existing overflow reproduction from #1216; its failure is not hidden.

### Settings IA — explicit loopback qualification

`llm_settings_probe.py --out <directory> --width 1366|1600` uses the real
MainWindow, settings widgets, TaskRunner and Core HTTP transport with
`simulation/common/llm_http_fixture.py`. Six synthetic POSTs exercise success,
401/404/429, late-result invalidation after reset and bounded timeout.
Opening, editing, prevalidation, remote-consent cancellation, reset and saving
make no extra POST. The recipe rejects HTTP remote/credential/query/fragment
URLs and invalid limits/key names, checks environment-name-only display and
needs-restart feedback after saving. The already sent delayed request is not
undone by reset; the UI ignores its stale result. No distant test is accepted.
Initial Tab focus is exercised; loss of focus after the disabled test button
is recorded. This does not certify screen-reader speech or live HTTPS servers.


### Recette P1 #4664 — actions exactes dans Recommandations

Les scénarios `recommendations-detail-1366` et
`recommendations-detail-1600` exécutent le plugin Recommandations et le
`PublicBookMasterDetailView` **réels** sous GTK/XTEST. Après la recette
de navigation #1354, le test clique au clavier sur
`recommendation.loaded-in-place.*` d’une édition connue **déjà chargée
dans Core Search**, passe par le port public `_UiHost`, et restitue
`DetailPanel.show_item_detail` avec les raisons de la recommandation.

Le bouton d’obtention est construit par le **même Core** que Recherche et
fait appel à `MainWindow.download_direct_item` : le clic met seulement la
tâche en file ; un résultat simulé est exécuté après vérification explicite
de cette file. Le test provoque aussi l’échec des disponibilités et
vérifie le repli `show_search(loading=False)`, les actions résiduelles,
l’absence de fuite d’erreur fournisseur et la révocation d’un ancien
bouton après redessin. Aucune demande réelle de droits, aucun GET fournisseur,
POST, achat, emprunt ou téléchargement ; aucun compte privé.

Cette recette prouve l’intégration GTK du **chemin chargé exact** et du
handler direct Core avec une fixture d’admission positive. Elle ne prouve
ni une offre réelle du fournisseur, ni toutes les variantes (mirroir v2,
droits possédés, prêts et multi-profils #4662), ni l’éligibilité et la
transaction sur un compte connecté. Ces critères restent ouverts sous #4664.

### Recette P1 #3687/#774 — Recommandations à 1024 × 768

Le scénario `recommendations-detail-1024` rejoue sous un **écran Xvfb réel
1024 × 768**, avec le plugin Recommandations et le composant CoreUI
`MasterDetailPage`. Le contrôle du probe impose une largeur GTK de
**924 à 1024 px** (et non une simple taille demandée à GTK) ainsi qu'une
capture effective 1024 × 768. Il hérite des validations Source multi-sélection,
Format/Type/Champ, filtre clavier XTEST, compteur `0 / N`, remise à zéro,
absence de recherche fournisseur induite et actions exactes avec protection
contre les callbacks obsolètes.

Ce scénario complète les recettes existantes à 1366 et 1600 px ; il ne
prouve pas à lui seul la conformité visuelle à toutes les densités ni le
HiDPI. Toute géométrie réelle hors du budget, action masquée ou échec clavier
est un **échec de recette**, non une autorisation d'agrandir artificiellement
la fenêtre ou d'ignorer l'étape. Voir #774 et #3687 pour la revue UX.
