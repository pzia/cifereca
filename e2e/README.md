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


### UX P1 #922 — aides et étiquettes des filtres Jeunesse (recette distincte)

La recette dédiée (1366 px uniquement) s'exécute avec :

    dbus-run-session -- /usr/bin/python3 e2e/run.py --scenario youth-filter-help-1366

Elle ouvre la vraie page GTK, inspecte les dix contrôles (profil, recherche,
âge, nature d'âge, source, langue, format, type, thèmes et âges inférés)
et vérifie une aide contextuelle non vide pour chacun. Les étiquettes doivent
être visibles et explicitement liées au widget via Gtk.Label.set_mnemonic_widget ;
la case à cocher peut porter son libellé intrinsèque.

Après un clic XTEST suivi de la saisie « Forest », le test vérifie la
persistance des étiquettes, la distinction cache local / collecte explicite
et l'absence de requête réseau. Il traverse également les contrôles avec
de vraies touches XTEST Tab/Shift+Tab, journalise les focus GTK réels (y
compris GtkText interne) et contrôle qu'aucune collecte n'est induite.
Le rapport screen.json et trois captures documentent les dix champs, le
parcours clavier et toute absence d'aide. Un contrôle non atteint ou un
double arrêt Tab anonyme sur un Gtk.FlowBoxChild non interactif est signalé,
sans déduire l'accessibilité de la seule capture visuelle.

**Test d'acceptation intentionnellement bloquant tant que le plugin n'a pas
ajouté les libellés et tooltips du P1 #922.** Les anciens scénarios Jeunesse
restent inchangés. Ne pas contourner le test par des placeholders.
Aucun scénario de largeur basse et aucun workflow CI nouveau.

## Recette Jeunesse P1 (#758)

La recette `catalog.youth` utilise la vraie page `ui.page.youth`, le Core et
le package versionné de `src/plugins/youth-catalog` installé dans le store
isolé de la fixture. Aucune requête réseau n'est déclenchée ; le cache est
alimenté directement avec des notices déterministes par identité.

```bash
dbus-run-session -- /usr/bin/python3 e2e/run.py --plugin catalog.youth
```

Un écran Xvfb **1366×900** est exercé. Les captures
`00-empty-cache` à `06-keyboard-search-detail` prouvent les états vides,
tranches 0/8/12/17 (18 non proposé), âges d'intérêt/lecture/inféré,
isolation Camille/Alex, recherche clavier XTEST, sélection et provenance.

Chaque rapport `screen.json` inclut le SHA Git, la résolution réellement
observée et les empreintes des captures. Il s'agit d'un parcours GTK4 réel
avec fixtures de cache, **pas** d'un test HTTP des fournisseurs ni d'un
parcours réseau. Les scénarios des fournisseurs et le cache offline après
collecte restent à recetter séparément.


### Milan activé — pagination, notice GTK et panne 503 (UX P1 #758)

Le scénario `youth-milan-1366` installent la **vraie
extension** `youth-source-milan` dans le store isolé, puis simulent uniquement
ses réponses HTTP avec `httpx.MockTransport` et les pages HTML versionnées
de `simulation/milan/pseudo_milan_service.py`. Aucun trafic vers Milan
n'est effectué.

Sur la vraie GTK/Xvfb, le test confirme l'absence d'accès implicite,
l'activation de Milan dans la liste des sources, le clic XTEST de collecte,
les **quatre notices acceptées**, la pagination explicite (une cinquième
notice), l'âge éditeur attesté et le **Gtk.LinkButton « Voir la notice
publique »** avec URL HTTPS vérifiée. Le bouton n'est **pas activé** :
l'ouverture d'un navigateur/portail et l'accès au site réel restent hors
périmètre. Après HTTP 503 simulé, les cinq notices restent dans le cache,
un message d'échec est visible et le filtre local ne déclenche aucun HTTP.

Cette recette vérifie Milan de manière autonome, avec transport HTTP simulé.


### Deux éditions homonymes, ISBN distincts et âges contradictoires (UX P1 #758)

La recette `youth-editions-1366` utilisent la
**vraie interface GTK4/Xvfb**, sans plugin distant ni réseau :

- 3 notices locales synthétiques ont exactement le même titre et la même
  autrice, mais deux ISBN différents ;
- Source fictive `5–7 ans` (intérêt) et Milan `à partir de 12 ans` (éditeur)
  partagent le **premier ISBN** : une seule ligne, **deux preuves d'âge
  conservées avec leurs sources** et visibles dans le détail ;
- un **second ISBN** Milan `à partir de 8 ans`, même titre/autrice, doit
  rester une **autre ligne** et n'afficher que son âge/source ;
- filtres 6, 9 et 12 ans, nature d'âge intérêt/éditeur, et identité Alex
  sans entrée, sont vérifiés sur la vraie liste/détail GTK.

8 captures permettent d'inspecter la distinction des éditions
et la provenance. Aucun rapprochement automatique par titre/autrice ne doit
faire fusionner des ISBN différents. Les fiches et ISBN sont fictifs ou
utilisés uniquement comme données de test structurées ; aucune disponibilité
éditoriale externe n'est attestée.

#### Visibilité des identifiants d'édition (UX #891, correctif produit #894)

Cette recette vérifie maintenant **l'affichage réel** des deux ISBN dans
les cellules `Gtk.ListView` mappées (`meta_label`, à 1366 px),
ainsi que les `Adw.ActionRow` de chacune des deux fiches sélectionnées.
Les ISBN sont distincts et ne fuient pas d'une fiche à l'autre. Le scénario
sans ISBN du cache local vérifie la mention **Référence source** dans la fiche,
sans faux ISBN. Les captures à 1366 px restent la preuve visuelle.

### Reprise explicite après panne distante (UX #758)

Le scénario `youth-milan-1366` vérifient désormais toute la séquence
**succès → HTTP 503 → cache conservé → filtre local sans requête →
nouveau clic XTEST après rétablissement → succès**. L'extension réelle
reste installée ; le simulateur ne remplace que les réponses HTTP.

Milan : 9 requêtes pour page 1, 3 pour page 2, 1 pour la panne,
9 après reprise explicite (22 au total). La reprise remplace le cache de
cinq notices par les quatre notices fraîches de la première page et
réactive la pagination, sans doublons ni perte silencieuse de provenance.

Les étapes supplémentaires sont capturées dans
`06-milan-recovered-first-page.png` à 1366 px. Ceci ne
prouve pas la disponibilité des services publics ni la reconnexion
réseau réelle : l'intégralité des réponses HTTP reste simulée.

### Notice publique : lancement réel sans trafic Internet (UX P1 #758)

Le scénario `youth-milan-1366` valident
désormais la **navigation GTK réelle** : après une collecte volontaire avec
le vrai adaptateur Milan et un transport HTTP simulé, un clic de souris XTEST
sur le vrai `Gtk.LinkButton « Voir la notice publique »` est envoyé.

Dans le HOME/XDG **temporaire isolé du runner**, le test crée un gestionnaire
d'URI HTTPS local via `Gio.AppInfo` et vérifie qu'il est bien le défaut
**avant** d'autoriser le clic. Ce faux navigateur écrit uniquement l'URL
reçue dans `local-https-handler.jsonl` et n'effectue aucune requête. La
recette vérifie une invocation **exactement** égale à l'URL Milan affichée,
aucun clic implicite et aucune modification du cache ni nouvelle collecte.

L'étape `02b-milan-notice-launched-to-offline-browser.png` et le JSON
`milan_notice_handler_invocations` prouvent le passage
**clic GTK → gestionnaire HTTPS local de test**. Ce protocole ne prouve
pas qu'un navigateur externe réel est installé ni la disponibilité du site
éditeur. Hors du XDG de test, aucune association HTTPS n'est modifiée.

### Activation : preuve GTK du gestionnaire de plugins (UX #758)

Le parcours local `youth-1366` se terminent
désormais par la **vraie page Plugins** de Cifereca, après l'installation
et l'activation du vrai package Jeunesse. L'utilisateur recherche
`youth` dans la zone GTK du gestionnaire par **saisie clavier XTEST**.
Le banc vérifie l'état activé affiché, le statut technique, l'absence
d'activation involontaire et l'absence de requête réseau.

Les captures `09-real-plugin-manager-installed.png`,
`10-real-plugin-manager-youth-search.png` et
`11-youth-restored-after-plugin-manager.png` documentent le parcours
**Jeunesse → Plugins → recherche jeunesse → retour Jeunesse**, aux
largeur 1366 px. Cette recette ne désinstalle pas de package
et ne prétend pas recetter une première installation manuelle par la
boîte de dialogue du sélecteur de fichiers.

### Observation clavier Entrée — écart UX P1 #910 (non bloquante)

Sur le scénario local `youth-1366`,
après une recherche `Forest` saisie **physiquement par XTEST**
dans le vrai `Gtk.Entry`, le banc émet `Return` puis `KP_Enter`.
Il compte les émissions du signal GTK `activate`, capture les statuts
visibles avant/après, la route et les notices affichées, et garantit
**zéro accès distant** sans source précise sélectionnée.

Nouvelles preuves : `05b-youth-return-gtk-observation.png` et
`05c-youth-keypad-enter-gtk-observation.png` à 1366 px, avec
`screen.json > keyboard_enter_observation` (deux signaux GTK, changement
ou non du retour utilisateur). La capture `06-keyboard-search-detail.png`
reste le parcours suivant.

**Après le correctif clavier #910**, ce probe exige désormais un retour
visible pour la première Entrée (cache local) et pour la seconde (demande
d'une source précise si « Toutes les sources » est sélectionné). Il interdit
toujours tout réseau implicite. Le PASS ne couvre pas encore, dans la vraie
fenêtre GTK, la collecte volontaire par une deuxième Entrée **avec une source
sélectionnée** : ce dernier scénario reste à recetter indépendamment.
Aucun nouveau scénario ni workflow CI n'a été créé.

### UX P2 #962 — focus clavier au retour Recherche → Jeunesse

Exécuter le parcours strict indépendant à **1366 px** :

```bash
dbus-run-session -- /usr/bin/python3 e2e/run.py --scenario youth-focus-return-1366
```

Le test utilise le même vrai trajet GTK/XTEST que le parcours P0 #906 :
sélection d'une édition Jeunesse, clic « Rechercher en bibliothèque », unique
recherche **locale** avec identité contrôlée, puis Retour GTK. Il capture
`14-library-search-return.png`, lit `window.get_focus()` et exige que
le focus soit sur le bouton d'origine ou son descendant, **avant tout appel
à `grab_focus()`**. Le contrôle vérifie aussi la sélection, le filtre,
l'identité et l'absence de collecte/recherche distante.

Ce scénario P2 est **rouge intentionnellement** tant que la navigation
ne restaure pas le focus. Le parcours P0 #906 reste inchangé et continue
de vérifier Entrée/Espace indépendamment ; aucune modification du Core,
des plugins ou des workflows et aucune cible à résolution inférieure.

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
