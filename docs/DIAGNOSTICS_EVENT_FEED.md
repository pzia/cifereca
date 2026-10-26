> **ATTENTION — état historique, et non contrat cible.** La décision impérative [LOGGING-CONTRACT.md](LOGGING-CONTRACT.md) du 25/10/2026 ([#2900](https://github.com/pzia/cifereca/issues/2900)) annule toute exigence de listes blanches, expurgations génériques, champs omis et résumés « masqués » décrite ci-dessous. Cible : Core, plugins, fichier, terminal, broker, recherche et export brut conservent toutes les données réellement émises ; **seul le rendu IHM** pseudonymise **login/mot de passe**, sans modifier les autres champs. Les dispositions historiques restent descriptives de l'implémentation existante, **pas des exceptions à la consigne**. La mise en conformité du code reste à réaliser séparément.

# P0 #1010 — Flux public Diagnostics, lecture bornée

Le broker public `context.broker("diagnostics")` expose désormais :

```python
feed = broker.events(
    source="all",            # all, core_events, app_log
    module="",               # préfixe du module
    severity="",             # debug, info, warning, error, critical
    since=None, until=None,  # ISO-8601 avec fuseau obligatoire
    limit=50, offset=0,      # limit entre 1 et 100, offset entre 0 et 10000
)
```

La réponse est un dictionnaire `status / sources / events / total / next_offset /
read_only / bounded`. Chaque source expose un statut explicite : `available`,
`empty`, `disabled`, `unavailable`, `partial`, avec une raison lorsqu'elle
est connue. Le champ facultatif `last_event` est l'horodatage UTC du **dernier
événement publié par cette source dans la fenêtre bornée du Core**, calculé
avant les filtres et la pagination. Il est absent si aucun événement publiable
n'a été trouvé ; il ne prouve ni l'exhaustivité de l'historique ni la fraîcheur
du fichier brut. Chaque événement expose `timestamp`, `source`, `module`,
`severity`, `message` (résumé expurgé), `event_id` pseudonymisé.
Les valeurs `status` des Diagnostics structurés sont normalisées.

**Sécurité :** le chemin du journal est imposé par le Core
(`cifereca.log` + rotations `.1/.2/.3`), jamais par le plugin. Fichiers
réguliers sans lien symbolique, lecture des dernières 32 Kio maximum par
rotation, 200 événements maximum par fichier, 500 événements structurés
maximum en mémoire. Aucune lecture de base SQLite, credential, ACSM,
stdout/stderr brut, autre répertoire, réseau ou opération fournisseur.

Les suffixes de rotation absents (`.1`, `.2`, `.3`) sont normaux
et n'affectent pas la couverture. En revanche, si un journal actif lisible
coexiste avec une rotation présente mais illisible (lien symbolique,
permissions, fichier non régulier), ou si seule une rotation est lisible sans
journal actif, la source est **`partial`** avec la raison publique
`log_source_incomplete`. Aucun chemin ni contenu refusé n'est exposé au
plugin. Une source totalement absente demeure `unavailable`, et une simple
troncation reste `partial` avec `log_tail_bounded`. La couverture ne prouve
jamais l'exhaustivité des rotations.

Pour les journaux libres, la sortie **ne restitue pas le texte original** :
les seules données conservées sont le module, le niveau, le timestamp et une
signature opérationnelle `TRACE` à vocabulaire borné, éventuellement enrichie
de champs non secrets sur liste blanche (`phase`, `status`, `outcome`…).
Un message libre est remplacé par « contenu libre masqué ». Pour les
Diagnostics structurés, seuls les mots opérationnels autorisés sont diffusés,
pas les détails ni les valeurs potentiellement personnelles, sauf la projection
fermée Numilog décrite ci-dessous. Les empreintes
d'événements ne réintroduisent aucune valeur brute. Pour les journaux
applicatifs, `event_id` est une empreinte de la date, du niveau,
du module expurgé, du résumé publié et de l'identité interne du fichier
ouvert (périphérique/inode et position
absolue en octets). Elle reste stable lors du renommage standard
`cifereca.log` vers `cifereca.log.1` et lors du déplacement de la fenêtre
de lecture bornée, sans exposer ces métadonnées dans l'API. Des lignes
identiques demeurent distinctes. Pour les diagnostics structurés, deux
enregistrements avec la même date, catégorie et opération reçoivent
également des identifiants distincts dans le snapshot publié (désambiguïsation
par ordre d'apparition). La stabilité n'est garantie que tant que ces
enregistrements restent présents dans cette même fenêtre du Core ;
l'éviction des premières entrées peut renuméroter des doublons.
**Limite :** une rotation par copie,
restauration ou réécriture du fichier peut changer cette empreinte ; elle
n'est pas un identifiant global ou une preuve de corrélation métier.

**Couverture multi-source :** le champ global `status` devient `partial`
si une source demandée est partielle, ou si une source consultable coexiste
avec une source `disabled` ou `unavailable`. Il reste `unavailable`
si toutes les sources sont désactivées ou indisponibles ; il indique
`empty` seulement sans événement ni source manquante et `available`
lorsque des événements sont publiés sans lacune signalée. Les états de
`sources[]` restent la référence pour savoir quelle source manque.
Ce résumé ne garantit jamais l'exhaustivité historique.

**Limites connues :** le flux est un **snapshot borné**, pas une archive
historique globale. Lorsque le journal est tronqué ou rotaté, son état est
`partial`; on ne prétend pas retrouver tous les événements. L'explorateur
#1013 peut afficher ces entrées sans inventer des informations manquantes.
Un examen local manuel des journaux complets reste distinct et ne donne
aucune permission de lecture arbitraire à un plugin.

Priorité P0 ; l'analyse IA #1009 n'est jamais déclenchée depuis ce broker.
Aucune nouvelle GitHub Action.

## Horodatages normalisés UTC (#2380)

Tous les `timestamp` des événements publiés et les `sources[].last_event`
sont des instants ISO-8601 avec décalage `+00:00`. Les événements historiques
du collecteur peuvent porter n'importe quel décalage explicite dans leur
snapshot ; la projection conserve **l'instant**, non sa représentation locale.
Une ligne de `cifereca.log` n'a pas de fuseau dans son format historique :
elle est interprétée dans le fuseau local du processus lecteur puis convertie
en UTC. Un changement de fuseau de la machine entre écriture et lecture
peut donc empêcher une reconstitution exacte de l'heure de ces anciens logs ;
il ne faut pas fabriquer une corrélation métier avec leur seul horodatage.

Filtres `since/until` et tri opèrent déjà sur les instants UTC. La
normalisation ne crée pas de `task_ref`, ne restaure pas les traces filtrées,
et n'augmente ni la rétention, ni le périmètre de collecte ou d'export.
L'interface reste libre d'afficher ces instants **en heure locale**, en
indiquant son fuseau. Les `event_id` des événements de collecteur sont
désormais calculés sur l'instant UTC canonique : pour une même entrée,
ils peuvent différer des versions antérieures du logiciel.

## Couverture des événements structurés

Le snapshot Core est limité aux **500 derniers enregistrements**. Quand il
contient davantage de lignes, ou qu'un enregistrement sans horodatage ISO
valide ne peut pas être publié, `core_events` rapporte `partial` et le code
fixe `snapshot_records_incomplete`. Les événements valides restent
consultables ; aucune donnée brute ni détail de l'enregistrement rejeté
n'est renvoyé. Un snapshot vide et valide reste `empty` avec
`no_recorded_events`. L'UI et l'aperçu IA utilisent la même liste autorisée
de codes de couverture.

## Projection Numilog (#1884)

Le module structuré `NUMILOG` reconnaît uniquement `acquisition_delivery` et
`session_state`. Son `detail` facultatif est reconstruit par le Core : aucune
copie du détail du collecteur. Pour la livraison, seules passent les valeurs
énumérées de `source`, `phase`, `code`, `mime`, `html_kind`, `origin_kind` et
`path_kind`, l'`action` aléatoire du producteur (12 hex minuscules),
`http_status` (100–599), `candidates` (0–10000), `hops` (0–12) et la durée
entière `duration_ms` (0–600000). Pour la session : raison fixe du producteur
et booléen exact `connected`. Les champs/valeurs/types inconnus sont omis,
sans conversion de texte libre. Une opération Numilog inconnue reste masquée.

Le `message` reprend ces seuls champs validés (500 caractères maximum), afin
que phase, cause et corrélation restent accessibles à l'explorateur et à son
export de sélection existants. `action` relie les étapes d'une tentative ; ce
n'est ni une identité de compte/livre ni une preuve de droit. Le statut et le
niveau du producteur sont conservés : `connected=False` n'est pas transformé
en refus d'identifiants, ni un manque de candidat en expiration de session.

Le journal reconnaît aussi le **logger exact** `cifereca_numilog`, uniquement
pour la ligne complète `TRACE numilog session connected=True|False reason=…`
avec une raison autorisée. Tout autre contenu de ce logger est masqué ; ses
sous-loggers et les autres loggers externes restent exclus. Cette source
locale demeure consultable si le collecteur structuré est désactivé, avec la
couverture partielle habituelle. Aucun HTTP ni appel fournisseur supplémentaire.

### Corrélation avec la tâche exécutante

Les événements `NUMILOG` enregistrés dans le `PriorityExecutor` partagé par
GTK `TaskRunner` et le broker de tâches portent un `task_ref` aléatoire de
12 hex minuscules, indépendant du compte, du livre, du label et de l'ID
numérique de tâche. Le producteur n'a aucune valeur à fournir. Le Core
reprend cette référence validée dans la projection publique et son message ;
`action` conserve son sens de tentative de livraison, plusieurs actions
pouvant appartenir au même worker.

Lors de la sortie de ce worker, le logger exact `cifereca` émet une ligne
fixe `TRACE task correlation task_ref=… status=success|failed|cancelled`,
également consultable dans `app_log`. Ce statut décrit le résultat du worker,
pas la validité de ses droits ni le succès de chaque phase fournisseur.
La projection refuse les références malformées et les suffixes libres.

La référence est allouée à la demande après les filtres du collecteur, sans
trace additionnelle pour les tâches sans diagnostic Numilog enregistré.
Le contexte est réinitialisé avant les callbacks de Future et la prochaine
tâche ; les workers concurrents ont des références différentes. Une panne
du générateur ou d'un handler de log facultatif ne change pas le résultat.
Les événements hors worker restent sans `task_ref` : aucune corrélation
n'est déduite du seul horodatage, thread, texte ou identité. Un collecteur
Numilog désactivé/filtré ne produit donc pas cette corrélation ; les raisons
fixes de session du journal restent consultables selon le contrat précédent.


### Preuve de formulaire de connexion (#1884 / #2024)

La projection accepte `login_form_detected` sans inférer une expiration du
cookie. `connected_initial` est exportable seulement comme booléen exact ;
la raison fixe `delivery_login_form_detected` conserve ensuite la suspension
`connected=False`, y compris via la trace de log strictement validée. Le code
historique `session_expired_confirmed` reste lisible pour les anciens exports.
Aucune URL, donnée de cookie ou réponse HTML n'est ajoutée au contrat.

### Actions ACSM BnF explicites (#2055)

Le Core publie un résultat terminal `LOAN / bnf_loan_action` par action
réellement exécutée : `stage_loan_token` (récupérer le candidat), puis
`import_acsm` seulement après une **seconde confirmation**. Le domaine
`loan_download` et le fournisseur `bnf-pret` permettent de retrouver ces faits
avec les filtres existants. Le succès du premier signifie **candidat ACSM
validé**, jamais EPUB/PDF obtenu ou droit BnF créé. L'import reste une
acquisition **manuelle**, même si son action est attribuée à BnF dans Diagnostics.

Le schéma fermé contient `schema_version=1`, `provider_id`, `action`, `phase`,
`status`, `reason_code`, `duration_ms` et, uniquement dans un véritable contexte
de tâche, `task_ref`. Les phases distinguent admission, compte/session,
staging local, livraison fournisseur, validation d'artefact, revalidation de
source et import ADEPT. **`provider_delivery` ne prouve pas qu'un GET ACSM a
été envoyé** : la relecture d'activité peut refuser le prêt avant ce GET.

Les motifs fournisseur sont acceptés par égalité exacte avec une liste fermée
(ex. `bnf_access_denied`, `bnf_loan_not_observed`,
`bnf_download_redirect_refused`, `bnf_invalid_loan_token`). Une exception libre
reste `bnf_loan_token_unavailable` ou `bnf_import_failed`. L'absence/ambiguïté
connue d'un profil donne `adept_profile_required|adept_profile_ambiguous`.
Ni texte d'exception, URL/HTML/XML, cookie, compte, prêt, titre, chemin ou profil
ne sont publiés. Aucune corrélation n'est fabriquée hors worker ou par date.

Seul l'émetteur Core typé pose le marqueur d'attestation ; un `Diagnostics.add`
générique ne peut entrer dans le domaine privilégié. Le logger exact
`cifereca` expose aussi un événement JSON canonique `EVENT bnf loan action` ;
le lecteur `app_log` valide entièrement ce schéma et rejette les suffixes ou
champs supplémentaires. Les messages GTK proposent une action adaptée et le
même code sûr, sans supposer la cause des tentatives historiques non tracées.

Les tests utilisent HTTPX MockTransport, SQLite et GTK/XTEST sur données
synthétiques. Ils ne valident pas un GET BnF réel ni le fulfillment Adobe réel.
