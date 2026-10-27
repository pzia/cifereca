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
