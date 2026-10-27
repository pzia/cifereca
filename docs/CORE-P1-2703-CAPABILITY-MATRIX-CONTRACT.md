# P1 #2703 — Contrat additif des preuves de capacités (v1)

**Statut : première tranche Core.** Ce contrat n'est pas la matrice complète des
fournisseurs et ne remplace ni `CatalogueSourceCapability` (#2385), ni
`LoanInventorySnapshot` (#3122), ni `SyncSourceRef.sync_capabilities`.

## API publique

`cifereca.plugin_api` expose :

- `CAPABILITY_MATRIX_CONTRACT = "capability-matrix/1"`
- `CapabilityEvidence` : une observation immuable **exacte** pour
  `provider_id × operation × scope × field × identity_id × account_id × edition_id`.
- `CapabilityMatrix` : collection non ambiguë, `exact(...)` sans repli
  implicite de champs, compte ou édition.
- `declared_provider_matrix(descriptor, enabled=..., connected=...)` :
  adaptateur des booléens du contrat actuel `ProviderCapabilities`, sans
  appel fournisseur et sans migration des plugins.

Les statuts `supported / partial / unsupported / unverified` sont distincts
de `implementation_state = implemented / prototype / planned / not_applicable`.
Un `supported` exige une preuve référencée par observation hors code et une
implémentation effective ; `partial` exige une raison, notamment pour les
post-filtres portant **seulement sur une page**.

`enabled` (installation), `connected` (session) et `status` (preuve) sont
orthogonaux. Par défaut, une déclaration de code positive projette
**`unverified`**, même si le plugin est installé/connecté ; une déclaration
absente projette `unsupported` **au titre de cette déclaration**, jamais un
verdict sur toute fonctionnalité future.

### Frontière de sécurité

**Aucune observation n'autorise une mutation.** Même `supported` reste une
preuve de lecture : `authorizes_mutation` vaut toujours `False`. Le droit
exact, la fraîcheur de la session, l'édition, le consentement et les verrous
sont vérifiés par les brokers/actions existants au moment de l'opération.

`ProviderSearchCapabilities.fields` et `.scopes` ne sont **pas** croisés :
une prise en charge de `title` dans la bibliothèque privée ne signifie pas
`title` dans le catalogue public. Le projecteur de déclarations ne produit
donc que des lignes `scope="unknown"` et **aucun filtre implicitement valide**.
Pour émettre un vrai couple supporté, fournir séparément une
`CapabilityEvidence` avec champ, portée et preuve exacts.

### Raccordements restants (hors de cette PR)

1. Adapter les trois contrats existants à une lecture consolidée par
   opération/compte/édition **sans** réinterpréter les droits ni créer de
   deuxième stockage.
2. Faire alimenter par les plugins uniquement les observations vérifiées dans
   leurs tickets propres (#1917, #1886, #3252, #3415).
3. Consommer cette projection dans CoreUI/Diagnostics après vérification de
   permissions, actions et états partiels ; ne jamais afficher une source
   désactivée comme une fonctionnalité active.
4. Exercices transverse 7 familles (catalogue, métadonnées, droits, prêt,
   acquisition, DRM, transversal), tests de compatibilité et recette UI.

**Contrat non activé automatiquement sur les providers existants** :
la fonction de projection est en lecture seule, explicite et sans I/O.

## Projection des contrats existants (#2703, tranche 2)

`project_existing_capabilities(descriptor, identity_id=..., catalogue=...,
loan_snapshot=..., sync_source=...)` compose **en mémoire seulement** les
trois contrats déjà livrés. Le Core doit d'abord sélectionner les snapshots
dans leur **scope identité/compte exact**, et la méthode ne fait elle-même
aucun GET, scan, acquisition, appel fournisseur ni modification SQLite.

- `CatalogueSourceCapability` (#2385) ne porte **aucune identité** dans son
  schéma v1 : le **caller** doit le sélectionner dans
  `catalogue_source_capabilities(identity_id)` de la bonne identité.
  `catalog_search_available=True` signifie ici « service connecté selon
  l'inventaire Core », **pas** « recherche live attestée » :
  `status=unverified`, jamais `supported`. La capacité distincte
  `catalog.refresh` reste `unsupported` : recherche ≠ snapshot.
- `SyncSourceRef.sync_capabilities` produit des observations de **code**
  `sync.remote_inventory`, `sync.entitlements`,
  `sync.local_materialization`, `sync.scheduled_refresh`, toutes
  `unverified`, même si le dernier `phase_state` indique `success`.
  Le `enabled=False` et `connected=False` restent visibles. Les noms
  inconnus ne sont pas promus en opérations.
- `LoanInventorySnapshot` (#3122) projette seulement son compte/credential
  exact et ses branches réellement déclarées. `supported` signifie ici
  **observation de réponse fournisseur** `status=success` avec
  `observed_at` et branche(s) `completeness=complete +
  pagination_complete=True` ; cette déclaration ne prouve **aucun** droit
  de mutation/expiration et ne prétend pas couvrir une autre branche.
  Toute preuve de couverture absente, partielle, périmée ou échouée reste
  `partial/unverified`.

Le tuple d'identité d'une observation contient maintenant **le credential
en plus du compte**, pour éviter qu'un même emprunteur sous deux connexions
distinctes soit confondu. Le champ est optionnel pour les anciennes
constructions v1, et doit être fourni lors de toute preuve liée à un compte.

**Ce projecteur n'est pas une fusion d'inventaires** : aucune jointure des
listes `fields` et `scopes`, aucun entitlement construit depuis les
métadonnées, aucune capacité à acquérir déduite d'une couverture observée.

Reste pour finir #2703 : service Core de lecture fédérée et CoreUI, preuves
fournisseur des 7 familles, recettes E2E exactes. L'absence de ces dernières
ne peut pas être masquée par cette projection technique.

## Inventaire Core sans I/O pour l'IHM (#2703, tranche 3)

`LibraryService.capability_matrices(identity_id=None)` publie un tuple de
`CapabilityMatrix` trié, par **source activée et enregistrée** pour l'identité
demandée. La valeur par défaut est l'identité courante. Une identité inconnue
ne reçoit **aucune** entrée. Le Core réemploie son inventaire de catalogues
`catalogue_source_capabilities` (catalogue public OU inventaire personnel),
sans créer de pseudo-compte public, sans activer de plugin et sans déléguer
de recherche au fournisseur. Ce premier point d'entrée CoreUI n'affiche
pas les plugins techniques, sources `skeleton` ou désactivées.

Par source, les opérations `catalog.search`,
`personal_inventory.list` et `catalog.refresh` sont projetées **sur
l'identité et le scope précis** `public/authenticated` tandis que les
huit opérations historiques de `ProviderCapabilities` restent en
`scope=unknown`, avec la seule preuve de **déclaration de code**.
Une session connectée n'élève jamais la capacité vers `supported`.
Chaque ligne `authorizes_mutation=False` : aucune autorisation de prêt,
achat, téléchargement ou synchronisation de catalogue ne suit de cette API.

**Restriction assumée :** le chemin par défaut ne lit pas
`SynchronizationBroker.sources()` car sa projection legacy est susceptible
de migrer des métadonnées persistées ; il ne fait pas non plus de
`read_loan_inventory()`, qui serait une requête fournisseur. Les snapshots
déjà acquis sont projetables séparément via
`project_existing_capabilities(..., sync_source=..., loan_snapshot=...)`
après sélection exacte de leur compte/credential. Le pont CoreUI pour ces
preuves historiques reste explicitement à terminer, ainsi que les 7
familles de capacités par source, sans élargir artificiellement les scopes.

## Lecture Core des preuves déjà collectées (#2703, tranche 4)

`LibraryService.capability_matrices_with_precollected_evidence(identity_id,
sync_sources=(...), loan_snapshots=(...))` complète la projection déclarative
existante, **uniquement** avec des snapshots déjà récupérés et sélectionnés
par le consommateur dans leur portée exacte. L'appel ne lit jamais le broker
Sync, le réseau ou un fournisseur, et n'effectue aucune migration SQLite.
En l'absence de snapshots, le résultat est identique à
`LibraryService.capability_matrices(identity_id)`.

Les observations `sync.*` gardent le statut `unverified/code`, les
snapshots de prêts complets produisent des états informatifs
`supported/account_observation` **pour le compte + credential exact**.
Deux credentials pour le même emprunteur sont distincts. Deux observations
pour **la même clé complète** échouent explicitement avec
`capability_duplicate_scope` : jamais de « dernier arrivé gagne ».
Un provider désactivé ou non admis par l'inventaire Core n'est pas ajouté
silencieusement ; les mélanges A/B et les types invalides sont refusés.

**Responsabilité du caller :** valider avant passage des snapshots leur
source, session/epoch/fraîcheur réelle et permissions de lecture. Même une
observation complète datant du passé **ne donne aucune autorisation** :
`authorizes_mutation=False` pour toutes les cases. Le point d'entrée
n'exécute aucune acquisition ou synchronisation et ne prétend pas attester
les sept familles, ni la recette CoreUI/GTK.


## Observation des droits déjà collectée (#2703, tranche 5)

`LibraryService.capability_matrices_with_precollected_evidence(...,
rights_snapshots=(...))` accepte maintenant, de façon additive, les
`ProviderRightsSnapshot` du contrat public `cifereca.rights.observation/1`
(#3252, adaptateurs Kobo et Numilog déjà livrés). Le consommateur fournit
seulement des snapshots **déjà collectés** et contrôlés ; cet appel ne
lit **ni** le plugin, **ni** les tokens, **ni** un fichier, ni SQLite, et
n'effectue aucune action.

Chaque snapshot reconnu donne une ligne `personal_inventory.rights`,
`scope=account`, `field=branch` dans la matrice de sa source **activée
et admise** pour l'identité choisie. La clé conserve les coordonnées
`identity_id / provider_id / credential_id / account_id / branch` ;
la connexion courante provient de Core et n'est pas déduite du snapshot.
Une autre identité ou une source désactivée est refusée, les doublons
à portée exacte échouent, deux comptes/credentials/branches restent
distincts. La référence de preuve exposée est un **identifiant de contrat
fixe**, jamais le `snapshot_ref` arbitraire d'un fournisseur.

L'état maximal de cette ligne reste **`partial`**, **même** si le
fournisseur déclare `coverage=complete` : il décrit un **inventaire de
droits observé**, pas une adjudication de possession, de copie locale, de
livraison, de révocation, ni une capacité d'acquérir. `failed/cancelled`
reste `unverified`. Aucune observation par édition n'est transformée
en permission d'action, `authorizes_mutation=False` partout. Les
branchements des snapshots restent optionnels et n'affectent pas les
consommateurs CoreUI existants.

**Restent #2703 :** présentation/recette CoreUI des sept familles et
attestations fournisseur réellement qualifiées. L'inventaire statique
et ce bridge n'en prétendent pas la couverture.
