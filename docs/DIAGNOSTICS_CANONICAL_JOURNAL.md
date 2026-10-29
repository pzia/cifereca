# Journal Diagnostics canonique — contrat de migration (#3132 / #3133 / #3135)

## Source d'autorité

Le fichier existant `cifereca.log` (répertoire d'état utilisateur) et ses
rotations numérotées `.1` à `.3` sont la source physique des événements
journalisés. Chaque occurrence est identifiée par l'identité locale du fichier
(device + inode) et son offset initial. Ni l'heure ni la similarité du message
ne constituent une preuve de doublon ou de causalité.

Le broker `diagnostics.events(search="...", limit=50,
offset=...)` (ou `source="journal"` explicite) lit les quatre fichiers conservés, applique les filtres avant
pagination et renvoie `total`, `next_offset`, des événements datés UTC et la
couverture de la fenêtre physique. Recherche insensible à la casse et aux
accents. Le paramètre optionnel `sort_order="asc" | "desc"` s'applique
**après les filtres globaux mais avant la pagination** ; `asc` reste le
défaut pour les anciens consommateurs. La page GTK « Plus récents d'abord »
utilise `desc` : son premier lot contient les 50 événements les plus récents
**de toutes les rotations**, et sa page suivante remonte dans le temps.
À timestamp égal, l'ordre physique est préservé (`asc`) ou inversé (`desc`),
jamais déterminé par le hash `event_id`. Les événements `Diagnostics.add` compatibles JSON sont sérialisés
dans ce même fichier sous une enveloppe `DIAG_JSON`; le journal conserve le
message et tous les champs d'origine, sans pseudonymisation ni inférence de
provider à partir des messages libres. Les autres logs Python déjà produits
par le Core, l'UI et les plugins sont lus directement, en conservant le texte
de leurs erreurs et tracebacks.

Le broker propose `export_raw_journal()`, export JSON de toutes les
occurrences encore conservées, avec mentions de trous et `atomic=false`
(la lecture n'est pas un snapshot gelé face à une rotation simultanée).
L'export est volontaire, jamais réalisé à l'ouverture de la page.

## Visibilité et conservation

**Seule l'IHM Diagnostics pseudonymise les valeurs des champs
login/username et password/mot de passe.** Le journal, la console,
le broker, la recherche et l'export brut ne masquent pas URLs, cookies,
tokens, identifiants, chemins, titres, informations techniques ou mots de
passe. Une copie de la vue affichée conserve ses pseudonymes.

Le journal actif et les rotations créées par le logger ont des permissions
locales `0600`. Avant export, l'IHM prévient explicitement que les données
brutes peuvent contenir des informations confidentielles, puis demande une
confirmation et une destination locale. Aucun transfert automatique.

## Rétention et limites réelles

Logger: 1 000 000 octets par fichier actif, trois rotations. Le lecteur
accepte au maximum 2 000 000 octets pour chaque fichier afin de refuser des
sources anormales ; les trous, dépassements ou rotations inaccessibles sont
signalés `partial`. La lecture n'interroge aucun fournisseur. Un événement
antérieur aux rotations disponibles ne peut plus être retrouvé.

Les anciens noms `source="all"`, `"core_events"` et `"app_log"`
restent provisoirement acceptés par le **broker public** comme alias stricts
de `journal`. Ils renvoient exactement les mêmes événements physiques et
identifiants, sans lecture supplémentaire, duplication ni cache mémoire.
Les filtres `domain`/`reason_code` des projections héritées sont refusés :
ils ne sauraient être attestés à partir des journaux bruts. Un `provider_id`
explicitement présent dans le `detail` structuré est également exposé au
premier niveau pour le filtrage exact et l'UI ; il n'est jamais inféré du
message ni du nom du logger. L'ancien module
`core.diagnostic_event_feed` subsiste uniquement pour les tests/consommateurs
historiques à retirer séparément.

## Migration des producteurs Core attestés — lot #3155

Les producteurs Core validant eux-mêmes leurs champs attachent désormais une
projection structurée à **leur LogRecord existant** : BnF Prêt, téléchargement
de prêt, tentatives/résultats de connexion, synchronisation source, ADEPT,
réconciliation, démarrage Numilog et décision d'ouverture Kobo. Le formateur
du handler physique/console encode cette projection en `DIAG_JSON` **à la
place de la représentation texte de ce même enregistrement** ; il n'émet
pas une seconde ligne. Les handlers anciens lisent toujours le message
original. Les champs attestés, le `task_ref` effectivement émis et l'ordre
des occurrences sont consultables après redémarrage.

Un échec Numilog et une décision Kobo `error`/ `partial` sont respectivement
émis en `ERROR`/`WARNING` plutôt qu'en `INFO` afin de ne pas disparaître
du journal quand le niveau effectif est `errors`.

## Paramètres → Diagnostic système (aperçu de compatibilité)

Le panneau Paramètres ne lit plus `Diagnostics._items` ni
`Diagnostics.text()`. Il affiche au maximum 100 dernières observations
**structurées** du broker canonique, plus les faits locaux de présentation.
Ce n'est pas l'ensemble des logs : la recherche, les rotations et l'export
complet sont accessibles dans la page Diagnostics.

Le panneau Paramètres utilise les **mêmes pseudonymes de session GTK**
que la page Diagnostics, exclusivement sur les valeurs explicites
login/mot de passe. Tokens, URLs, titres, chemins et autres champs restent
visibles. La copie et « Exporter l'affichage » reprennent exactement ce
texte présenté. Cet export volontaire se fait en `0600` (même à
l'écrasement) et sans suivre de lien symbolique. L'**export brut du journal**
reste disponible, sans aucune transformation, depuis la page Diagnostics.
« Effacer l'affichage » ne supprime ni ne réécrit le journal et n'efface
plus un cache mémoire trompeur.

## Points restant à consolider

Les autres producteurs spécialisés et intégrations de plugins doivent être
inventoriés et vérifiés, et les anciennes projections/exports réduits
supprimés après migration de leurs lecteurs et des tests associés.
Les loggers indépendants de `cifereca` ne sont couverts que s'ils écrivent
dans le handler de journal configuré. Toute déclaration de couverture
exhaustive serait fausse. Pas de fusion de traces par heure, provider ou
message ; pas de diagnostic réseau implicite.

## Source par défaut du broker (#3133)

La source par défaut de `DiagnosticsBroker.events()` et du protocole public
`DiagnosticsBroker` est désormais `journal` : même pour les appels sans
paramètre `source`, la consultation est persistante et ne consulte pas le
snapshot mémoire. Les noms `all`, `core_events`, `app_log` sont tolérés
uniquement comme alias physiques rétrocompatibles, sans requête en mémoire. Les fonctions du plugin Diagnostics `fetch_explorer_page()` et
`collect_filtered_selection()` utilisent également `journal` par défaut ;
les tests des canaux historiques déclarent explicitement `source="all"`.
Leur disparition nécessite une migration distincte des appelants.


## Compatibilité broker : snapshot et export texte (#3133)

Le contrat `DiagnosticsBroker.snapshot()` renvoie désormais les **100 dernières
occurrences structurées** du journal physique (ordre chronologique), avec
`event_id` identique à `events(source="journal")`. Il n'ouvre jamais le
snapshot mémoire ni n'active de fournisseur. Les logs applicatifs libres restent
interrogeables par `events()`, mais ne sont pas déguisés en diagnostics
structurés. La rétention réelle peut être inférieure à 100 événements ; un
journal absent donne un tuple vide, sans repli sur la mémoire.

Le contrat `DiagnosticsBroker.export_text()` sérialise les **mêmes événements
physiques** en NDJSON lisible dans un éditeur, précédé d'un en-tête de version
et d'indications de couverture/rotations. La limite de l'export brut est
conservée. **Aucune expurgation** des login, mots de passe, cookies, jetons,
URLs, titres ou chemins n'est appliquée à ce texte ; l'export est local et
explicite. Le rapport de support (`build_support_report`), les contrôles de santé et
`Diagnostics.text()` conservent aussi les valeurs brutes depuis #3172/#3173.
Les helpers historiques `redact()` / `redact_text()` deviennent des identités
avec #3181 ; seule la page GTK Diagnostics pseudonymise au rendu les valeurs
explicites login/mot de passe.

Le cache `Diagnostics._items` et la fonction de projection
`query_diagnostic_events()` restent présents uniquement pour des contrats
historiques et leurs tests, mais **ne sont plus appelés par le broker public**.
Leur suppression physique (et celle des alias) est le prochain lot de #3133.

## #3649 — export brut du jour en ZIP (P0)

Dans **Diagnostics → Journaux**, utiliser **« Exporter le journal brut du jour
(ZIP)… »** : premier clic = avertissement sur les credentials et données
personnelles, deuxième clic = sélecteur GTK4 SAVE avec une fenêtre parente
réelle, puis écriture en tâche de fond. Aucune requête fournisseur ni envoi
réseau. Le nom proposé est
`cifereca-journal-brut-YYYYMMDD-HHMMSS.zip`; choisir un nouveau nom si le
fichier existe déjà. **Après une publication confirmée par le broker Core,
la notification affiche le chemin absolu exact du ZIP enregistré.** Une
annulation ou un échec ne présente jamais un chemin choisi comme un fichier
réussi ; le chemin ne quitte pas la session IHM ni ne s'ajoute au journal.
L'action est désactivée si le Core installé ne possède
pas le broker facultatif `export_today_journal_zip`.

L'archive contient `cifereca-journal-brut.json` (même schéma non masqué que
l'export JSON existant), `manifest.json` et `README.txt`. Le manifeste
atteste la date locale, le **nom IANA** du fuseau, le début UTC inclus, la
fin UTC exclue, le nombre d'événements et l'empreinte SHA-256 des octets JSON.
Les changements d'heure donnent une durée de 23 ou 25 heures ; il n'y a pas
d'hypothèse de journée fixe de 24 heures. La date est celle de l'export
effectif, non du dernier événement enregistré. Les événements antérieurs et
futurs sont exclus, même s'ils sont encore conservés sur disque. Aucun filtre
de l'écran (recherche, gravité, fournisseur, pagination) n'est appliqué.

Le ZIP est créé en fichier temporaire `0600`, fermé puis rendu visible en
une publication **exclusive** ; un ancien fichier ou symlink n'est jamais
écrasé. Annulation et erreur d'I/O enlèvent le temporaire. Le lecteur source
garde sa borne de **2 Mo par rotation** ; une rotation absente, inaccessible ou
trop volumineuse est indiquée explicitement `coverage=partial` avec
`rotation_gaps`. L'instantané n'est pas atomique face aux nouveaux messages
(`atomic=false`), et sa rétention ne prouve pas les événements antérieurs
déjà purgés. Si aucun fuseau IANA n'est disponible, refuser l'export plutôt
que produire de fausses bornes DST. Les destinations locales sont seules
prises en charge, sans transfert automatique.

**Ne jamais joindre directement une archive privée à une issue publique.**
Le ZIP contient des faits bruts, potentiellement passwords, cookies, tokens,
URL signées, identités et chemins. Vérifier avant tout partage ; une éventuelle
archive **expurgée** serait une action distincte, non implémentée ici.

L'ancien bouton **« Exporter tout l’historique (JSON)… »** est conservé dans
**Filtres avancés** pour les usages existants. Il exporte explicitement les
rotations entières, sans les confondre avec le ZIP du seul jour courant.

Tests : `tests/test_core_p0_3649_diagnostic_daily_zip.py` couvre DST
(23/25h), zéro événement hors jour, ZIP/manifeste/SHA, secrets inchangés,
rotations partielles, fichier existant/symlink, concurrence et annulation.
`src/plugins/diagnostics-page/tests/test_daily_journal_export.py` valide
la sélection sans Core réseau ; `test_daily_journal_export_gtk.py` exécute
le sélecteur GTK4 réel lorsqu'un DISPLAY est disponible.
