# Plugins P0 #3628 — producteurs de prêts typés (enfant de #3197)

État de réception : 28 octobre 2026. Contrat public inchangé :
`cifereca.loan.inventory/1` (`LoanInventoryScope`,
`LoanBranchCoverage`, `LoanInventorySnapshot`). Le périmètre B
est exclusivement le **producteur Plugins**. Le Core #3197 reste
propriétaire de la lecture, des caches, de l'UI, de la Sync et de la
suppression future du fallback v1.

## Recensement des packages installables

La recherche des capacités `sync.loans` dans les manifests
`src/plugins/*/cifereca-plugin.json` trouve **deux** producteurs
`provider.library` :

| Package / entrypoint | Capacité manifeste | Snapshot typé en production | Façade v1 conservée | Preuve de couverture |
|---|---|---|---|---|
| `provider-paris/cifereca_paris:PLUGIN` | `sync.loans`, API `>=1.4.1,<2` | `loan_inventory_snapshot(account)` ; une lecture verrouillée DILICOM+SYRACUSE, couverture `unknown` quand joignable, `partial` si branche en panne, aucune absence exhaustive | `loans(account)` reste utilisable ; seule cette façade met à jour `last_loan_errors`, sans jamais en déduire un état/une absence typés | tests `test_p0_3628_paris_typed_independent_v1.py`, tests compte/rebind/branch/cancel/HTTP existants |
| `provider-bnf-pret/cifereca_bnf_pret:PLUGIN` | `sync.loans`, API `>=1.4.1,<2` | `loan_inventory_snapshot(account)` ; lecture HTML verrouillée directement par `_read_live_loans_locked`, statut `partial` même si page vide, branche `bnf_html/digital` non exhaustive | `loans(account)` reste un alias public sûr vers le même producteur scoppé ; la lecture typée ne l'appelle plus | tests `test_p0_3628_bnf_typed_independent_v1.py`, tests d'epoch, reconnect, wrong-owner et ACS4 existants |

Les providers installables Kobo/Numilog sont **achats**, pas producteurs
`sync.loans` ; les plugins catalogue / transport / diagnostic ne
publient pas cette capacité. `runtime-kobo-adept` a
`loan.inventory` et `loan.inventory.readonly` (inventaire ADEPT
local), **pas** `sync.loans` de prêt fournisseur : son API de runtime
n'est pas substituée au `LoanInventorySnapshot` du compte Paris/BnF.

## Invariants des producteurs

- **Identité exacte** : scope `identity_id/provider_id/credential_id/account_id`
  figé, compte lié unique ; refus avant GET sur compte étranger et après
  lecture sur mutation, remplacement de session ou reconnexion A→A/A→B.
- **Une seule lecture fournisseur par branche** : Paris DILICOM puis
  SYRACUSE, BnF une page HTML. Le lecteur typé n'appelle jamais
  `loans()` v1 ; le fallback vers v1 après échec typé est interdit.
- **Preuves positives uniquement** : snapshot `partial`, `failed`
  ou `cancelled` n'atteste aucune disparition ; même `success` Paris
  avec deux branches répondantes a couverture `unknown`, pagination
  non complète. Aucune écriture d'OriginLink ou d'Artifact.
- **Erreurs typées** : branche Paris défaillante traduite en
  `branch_unavailable` dans le snapshot courant, issue du contexte de
  lecture scoppé. `last_loan_errors` est conservé **uniquement comme
  compatibilité observable v1**, et n'est ni consulté ni réécrit
  par la production du snapshot typé. BnF ne possède pas ce canal.
- **Passage à v2 sans rupture v1** : l'existence des méthodes est
  additive à Plugin API `>=1.4.1,<2`. Aucun retrait de `loans()` ni
  changement du format `cifereca.loan.inventory/1` dans cette tranche.

## Handoff obligatoire A/Core #3197

Les consommateurs restant sous responsabilité exclusive de A sont :

1. `src/cifereca/core/sync_coordination.py::_sync_loan_inventory_observation` :
   branche v1 `_loans_for_account(force=True)` à conserver jusqu'à
   migration/annonce de compatibilité des fournisseurs installés
   antérieurement. La seule liste v1 reste `legacy_unverified/partial`.
2. `src/cifereca/services.py::_loans_for_account` : façade de cache,
   lecture GUI et compatibilité ; différencier les clients typés des
   sessions héritées sans deuxième GET.
3. `src/cifereca/core/loan_return_verification.py` et les parcours de
   retour/renouvellement : refuser toute preuve négative issue de v1.
   Le pilote A doit conserver la validation scoped et la preuve
   `branch_claims_complete` quand il retire les chemins restants.
4. `src/cifereca/plugin_api/loan_inventory.py::read_loan_inventory` :
   repli contractuel `session.loans(account)` pour extensions v1
   **non livrées dans ce dépôt** ; son retrait nécessite une évolution
   de version Plugin API explicitement annoncée et un plan de migration.

**Ne pas supprimer ces voies Core dans #3628.** Recettes réelles de prêts
et ADEPT, droits, consentement, base SQLite et publication canonique
restent à #3171/#3228. Aucun vrai emprunt/retour ni téléchargement
n'est déclenché par les tests de ce document.
