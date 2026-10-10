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

## Recette Jeunesse P1 (#758)

La recette `catalog.youth` utilise la vraie page `ui.page.youth`, le Core et
le package versionné de `src/plugins/youth-catalog` installé dans le store
isolé de la fixture. Aucune requête réseau n'est déclenchée ; le cache est
alimenté directement avec des notices déterministes par identité.

```bash
dbus-run-session -- /usr/bin/python3 e2e/run.py --plugin catalog.youth
```

Deux écrans Xvfb **1024×900** et **1366×900** sont exercés. Les captures
`00-empty-cache` à `06-keyboard-search-detail` prouvent les états vides,
tranches 0/8/12/17 (18 non proposé), âges d'intérêt/lecture/inféré,
isolation Camille/Alex, recherche clavier XTEST, sélection et provenance.

Chaque rapport `screen.json` inclut le SHA Git, la résolution réellement
observée et les empreintes des captures. Il s'agit d'un parcours GTK4 réel
avec fixtures de cache, **pas** d'un test HTTP BookTrust/Milan ni d'un
parcours réseau. Les scénarios des fournisseurs et le cache offline après
collecte restent à recetter séparément.


### BookTrust activé — succès et panne 503 (UX P1 #758)

La commande suivante sélectionne les **quatre** recettes Jeunesse (les deux tests cache
et les deux tests BookTrust, sur 1024 et 1366 px) :

```bash
dbus-run-session -- /usr/bin/python3 e2e/run.py --plugin catalog.youth
```

Les scénarios `youth-booktrust-1024` et `youth-booktrust-1366` installent le
**vrai package** `youth-source-booktrust` avec `core.plugins.install()`,
aux côtés de `youth-catalog`. Ils exécutent la vraie page GTK et cliquent
sur « Collecter cette source » via XTEST. La réponse HTTP passe par
`httpx.MockTransport` (HTML provenant du simulateur versionné
`simulation/youth_sources/`) : **aucun appel au site BookTrust** et aucune
preuve d'état du site réel ne sont revendiqués.

Les captures/rapports vérifient successivement l'absence d'accès réseau lors
de l'ouverture/du choix de source, la collecte volontaire de deux notices,
la panne HTTP 503 sur une seconde collecte volontaire, la conservation du cache
et le filtre local produisant un résultat vide sans nouvelle requête.
Cela ne couvre pas encore l'indisponibilité d'une source distante en production,
ni la navigation effective vers la notice dans le navigateur.


### Milan activé — pagination, notice GTK et panne 503 (UX P1 #758)

Les scénarios `youth-milan-1024` et `youth-milan-1366` installent la **vraie
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

Cette recette complète celle de BookTrust. Elle exige le merge préalable
de la PR BookTrust #879 lorsque la PR Milan est empilée sur sa branche.


### Deux éditions homonymes, ISBN distincts et âges contradictoires (UX P1 #758)

Les recettes `youth-editions-1024` et `youth-editions-1366` utilisent la
**vraie interface GTK4/Xvfb**, sans plugin distant ni réseau :

- 3 notices locales synthétiques ont exactement le même titre et la même
  autrice, mais deux ISBN différents ;
- BookTrust `5–7 ans` (intérêt) et Milan `à partir de 12 ans` (éditeur)
  partagent le **premier ISBN** : une seule ligne, **deux preuves d'âge
  conservées avec leurs sources** et visibles dans le détail ;
- un **second ISBN** Milan `à partir de 8 ans`, même titre/autrice, doit
  rester une **autre ligne** et n'afficher que son âge/source ;
- filtres 6, 9 et 12 ans, nature d'âge intérêt/éditeur, et identité Alex
  sans entrée, sont vérifiés sur la vraie liste/détail GTK.

8 captures par largeur permettent d'inspecter la distinction des éditions
et la provenance. Aucun rapprochement automatique par titre/autrice ne doit
faire fusionner des ISBN différents. Les fiches et ISBN sont fictifs ou
utilisés uniquement comme données de test structurées ; aucune disponibilité
éditoriale externe n'est attestée.

#### Visibilité des identifiants d'édition (UX #891, correctif produit #894)

Cette recette vérifie maintenant **l'affichage réel** des deux ISBN dans
les cellules `Gtk.ListView` mappées (`meta_label`, y compris à 1024 px),
ainsi que les `Adw.ActionRow` de chacune des deux fiches sélectionnées.
Les ISBN sont distincts et ne fuient pas d'une fiche à l'autre. Le scénario
sans ISBN du cache local vérifie la mention **Référence source** dans la fiche,
sans faux ISBN. Les captures aux deux largeurs restent la preuve visuelle.

### Reprise explicite après panne distante (UX #758)

Les scénarios existants `youth-booktrust-1024/1366` et
`youth-milan-1024/1366` vérifient désormais toute la séquence
**succès → HTTP 503 → cache conservé → filtre local sans requête →
nouveau clic XTEST après rétablissement → succès**. L'extension réelle
reste installée ; le simulateur ne remplace que les réponses HTTP.

BookTrust : exactement trois requêtes, une par clic ; les deux notices
restent visibles et le message d'échec disparaît après reprise.
Milan : 9 requêtes pour page 1, 3 pour page 2, 1 pour la panne,
9 après reprise explicite (22 au total). La reprise remplace le cache de
cinq notices par les quatre notices fraîches de la première page et
réactive la pagination, sans doublons ni perte silencieuse de provenance.

Les étapes supplémentaires sont capturées dans
`04-source-recovered-after-503.png` et
`06-milan-recovered-first-page.png` aux deux résolutions. Ceci ne
prouve pas la disponibilité des services publics ni la reconnexion
réseau réelle : l'intégralité des réponses HTTP reste simulée.

### Notice publique : lancement réel sans trafic Internet (UX P1 #758)

Les deux scénarios `youth-milan-1024` et `youth-milan-1366` valident
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

Les parcours locaux `youth-1024` et `youth-1366` se terminent
désormais par la **vraie page Plugins** de Cifereca, après l'installation
et l'activation du vrai package Jeunesse. L'utilisateur recherche
`youth` dans la zone GTK du gestionnaire par **saisie clavier XTEST**.
Le banc vérifie l'état activé affiché, le statut technique, l'absence
d'activation involontaire et l'absence de requête réseau.

Les captures `09-real-plugin-manager-installed.png`,
`10-real-plugin-manager-youth-search.png` et
`11-youth-restored-after-plugin-manager.png` documentent le parcours
**Jeunesse → Plugins → recherche jeunesse → retour Jeunesse**, aux
largeurs 1024 et 1366 px. Cette recette ne désinstalle pas de package
et ne prétend pas recetter une première installation manuelle par la
boîte de dialogue du sélecteur de fichiers.

### Observation clavier Entrée — écart UX P1 #910 (non bloquante)

Sur les deux scénarios locaux `youth-1024` et `youth-1366`,
après une recherche `Forest` saisie **physiquement par XTEST**
dans le vrai `Gtk.Entry`, le banc émet `Return` puis `KP_Enter`.
Il compte les émissions du signal GTK `activate`, capture les statuts
visibles avant/après, la route et les notices affichées, et garantit
**zéro accès distant** sans source précise sélectionnée.

Nouvelles preuves : `05b-youth-return-gtk-observation.png` et
`05c-youth-keypad-enter-gtk-observation.png` à chaque largeur, avec
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

### Entrée : collecte volontaire avec BookTrust sélectionné (UX P1 #910)

Les scénarios existants `youth-booktrust-1024` et
`youth-booktrust-1366` exercent maintenant les **deux vrais chemins
clavier sur `Gtk.Entry`**, sans inventer de fournisseur et sans changer le
transport HTTP de test :

- Le plugin BookTrust réel est installé, sa source explicitement choisie.
  Le texte `the` est saisi **physiquement par XTEST** et correspond aux
  deux notices synthétiques existantes. Sans Entrée, zéro HTTP.
- **Return** : validation locale, `0 résultat(s)` et indication de la
  seconde Entrée, **zéro HTTP** ; capture `00b-booktrust-return-local-no-http`.
- **KP_Enter** : le même champ émet son second `activate`, **une seule
  collecte explicite** (une requête `https://www.booktrust.org.uk/...` dont
  la réponse provient de `httpx.MockTransport`), deux notices en cache ;
  capture existante `01-source-success-cache`.
- **Troisième Return**, même filtre/identité/source : aucun doublon
  HTTP, message visible d'une collecte déjà demandée ; capture
  `01b-booktrust-third-enter-no-duplicate`. Le clic suivant sur le
  bouton Collecter conserve les tests existants de panne HTTP 503,
  cache et reprise explicite (trois requêtes au total).

Le rapport `screen.json` inclut les activations GTK, les messages de
chaque étape et la liste bornée des requêtes. La recette n'utilise **aucun
réseau public** et ne prouve pas la disponibilité de BookTrust. Elle
complète les deux premiers scénarios `youth-1024/1366` déjà validés
pour Return puis KP_Enter **sans source précise**.

Aucun scénario supplémentaire, aucune règle CI ajoutée.
