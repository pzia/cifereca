# #3628 · Producteurs de prêts installables — migration vers l'inventaire typé

## Périmètre vérifié (30 octobre 2026)

Ticket enfant Plugins P0 de [#3197](https://github.com/pzia/cifereca/issues/3197),
sous l'architecture #3171. Le Core garde **exclusivement** le moteur Sync,
les consommateurs, le stockage canonique, le consentement, le retour et la
politique de suppression des droits.

| Producteur / service installable | Exposition legacy | Observation typée en production | Garantie négative |
| --- | --- | --- | --- |
| `provider-paris` (`ProviderSession`, `capabilities.loans=True`) | `loans(Account)` positif compatible ; aucun `last_loan_errors` depuis #3759 | `loan_inventory_snapshot(Account) -> LoanInventorySnapshot` sous verrou : une lecture DILICOM/digital et SYRACUSE/physical, compte/credential/identité et instance vivante figés, evidence de branche locale au contexte de la lecture. **N'appelle pas `loans()` et ne lit/écrit pas `last_loan_errors`** depuis la voie typée. | **Aucune** : succès des deux branches `completeness=unknown`, échec isolé `partial/branch_unavailable`, deux branches en panne refusées |
| `provider-bnf-pret` (`ProviderSession`, `capabilities.loans=True`) | `loans(Account)`, pas de canal `last_loan_errors` | `loan_inventory_snapshot(Account) -> LoanInventorySnapshot` sous verrou epoch/session, compte lié exact, une seule lecture HTML BnF, **indépendante de la façade v1** ; prêt positif et action ACS4 observables sans récupération du token | **Aucune** : HTML `active-loans` même vide toujours `partial/inventory_not_exhaustive` |
| `runtime-kobo-adept` | `loans(profile)` du moteur technique local ADEPT, **pas** `ProviderSession.loans(Account)` | Inventaire de runtime local distinct ; **pas un producteur de prêts fournisseur** dans la sélection Core `ProviderSession` | Aucune preuve de restitution fournisseur déduite des licences locales |
| Kobo, Numilog, autres providers catalogue/public installables | pas de `ProviderCapabilities.loans=True` identifié en tant que producteur `LoanInventorySnapshot` | Achats : contrat distinct `cifereca.rights.observation/1` ; aucune conversion de liste descriptive en prêt | Aucune |

L'API publique versionnée `cifereca.loan.inventory/1` et le modèle
`LoanInventorySnapshot` restent inchangés. Les deux providers de prêts
font **un seul passage fournisseur** par requête ; il n'y a pas de deuxième
GET `loans()` déclenché par la voie typée.

## Conformité et compatibilité

- Les tableaux `LoanInventorySnapshot` sont des copies défensives scopées
  (`identity_id/provider_id/credential_id/account_id`), couverture par
  branche figée, état `success/partial/failed/cancelled` explicite.
- Paris : le statut de chaque branche est collecté dans un
  `ContextVar` pour **cette lecture**, sous verrou. Les exceptions
  fournisseur DILICOM ou SYRACUSE ne deviennent pas une liste négative.
  La façade mutable `last_loan_errors` est **supprimée** depuis
  [#3759](https://github.com/pzia/cifereca/pull/3759) ; la méthode
  `loans(Account)` reste compatible pour les prêts positifs sans
  attestation négative. L'annulation arrête les lectures sans fabriquer de
  résultat complet.
- BnF : typé et legacy partagent seulement un lecteur interne verrouillé ;
  le modèle de droits positifs reste identique. Un HTML 200 vide ne suffit
  pas à prouver un retour de prêt.
- Aucune offre `EbookUrl`, notice, titre, ISBN, chemin ACSM ou action HTTP
  ne produit implicitement une présence locale, une absence de droit, un
  `OriginLink`, une acquisition ni une confirmation de retour.

**Tests de réception Plugins** (clients synthétiques, sans compte réel) :
`provider-paris/tests/test_p0_3628_paris_typed_no_v1_errors.py`,
`provider-bnf-pret/tests/test_p0_3628_bnf_typed_producer_independent_v1.py`,
plus suites antérieures de 0/1/N, erreurs HTTP, réseau interrompu,
Session `connect/close`, rebind A→B/A→A, identité/credential croisés,
deux branches indépendantes, protection contre lecture GET en double,
cache et sorties typées partielles. Les assertions des anciens tests de
course ont été déplacées sur le lecteur privé commun plutôt que de
réintroduire le couplage métier au lecteur v1.

## Handoff borné à l'agent A · #3197

**Rester à enlever chez Core seulement après vérification de la
compatibilité des extensions installées et migration/major Plugin API :**

1. `src/cifereca/core/sync_coordination.py` :
   `_sync_loan_inventory_observation` choisit encore un lecteur
   `_loans_for_account(..., force=True)` si le snapshot typé manque.
2. `src/cifereca/services.py` : `_loans_for_account` conserve le
   fallback `session.loans(account)` et le cache v1.
3. `src/cifereca/core/loan_return_verification.py` :
   `_read_paris_return_presence` accepte encore les positifs legacy,
   sans jamais les traiter comme une preuve négative.
4. `src/cifereca/plugin_api/loan_inventory.py` :
   `read_loan_inventory` adapte encore les anciens plugins non typés
   via `legacy_loan_inventory(..., status="partial")`.

**Ne pas supprimer ces fallbacks dans #3628** : il faut d'abord une
règle de compatibilité explicite pour tous les plugins installés,
un chemin de migration/rollback, et les preuves Core Sync/ouverture/
retour correspondantes. Le P0 parent #3197 reste piloté par A.
