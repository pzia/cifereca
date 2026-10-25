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
accents. Les événements `Diagnostics.add` compatibles JSON sont sérialisés
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

Le journal historique `source="all" / "core_events" / "app_log"` reste
temporairement disponible comme façade de compatibilité. Cette ancienne
projection, avec ses restrictions, **n'est pas une autre source d'autorité**.
Elle ignore les nouvelles lignes `DIAG_JSON` pour éviter leur double
comptage. Les consommateurs doivent migrer vers `source="journal"`.

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
snapshot mémoire. Les sources `all`, `core_events`, `app_log` restent
accessibles **seulement par sélection explicite**, pour les anciens clients
et tests. Les fonctions du plugin Diagnostics `fetch_explorer_page()` et
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

Les sources legacy `all`, `core_events`, `app_log` et le cache mémoire
`Diagnostics._items` restent des mécanismes transitoires. Les retirer après
migration de leurs consommateurs reste nécessaire avant de fermer #3133.
