# Journal Diagnostics canonique — contrat de migration (#3132 / #3133 / #3135)

## Source d'autorité

Le fichier existant `cifereca.log` (répertoire d'état utilisateur) et ses
rotations numérotées `.1` à `.3` sont la source physique des événements
journalisés. Chaque occurrence est identifiée par l'identité locale du fichier
(device + inode) et son offset initial. Ni l'heure ni la similarité du message
ne constituent une preuve de doublon ou de causalité.

Le broker `diagnostics.events(source="journal", search="...", limit=50,
offset=...)` lit les quatre fichiers conservés, applique les filtres avant
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

## Points restant à consolider

Les producteurs Core spécialisés qui n'écrivent encore qu'en mémoire doivent
être branchés au journal, et les anciennes projections/exports réduits
supprimés après migration de leurs lecteurs et des tests associés.
Les loggers indépendants de `cifereca` ne sont couverts que s'ils écrivent
dans le handler de journal configuré. Toute déclaration de couverture
exhaustive serait fausse. Pas de fusion de traces par heure, provider ou
message ; pas de diagnostic réseau implicite.
