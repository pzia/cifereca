# Core P1 #3119 — contrat additif d'inventaire de prêts

> **Document historique/partiel — 28/10/2026.** inventaire de prêts v1, observations positives par branche, pas droit à acquérir. La cible normative est la **synchronisation automatique unique** décrite dans [ARCHITECTURE.md](ARCHITECTURE.md) et [#3171](https://github.com/pzia/cifereca/issues/3171) ; le rattrapage manuel #2797 reste exceptionnel. Conserver les gardes existants ; ne pas déduire qu'un OriginLink canonique/transaction Core est livré de cette seule recette.

**Statut : première tranche d'API, non activée dans la réconciliation.** API publique
`cifereca.plugin_api.loan_inventory`, réexportée depuis `cifereca.plugin_api`.
Le fournisseur historique conserve `ProviderSession.loans(account) -> list[Loan]`.

## Sémantique du contrat `cifereca.loan.inventory/1`

- `LoanInventoryScope` : `identity_id` + `provider_id` +
  `credential_id` + `account_id`, toutes exactes. Le compte Paris principal
  `account_id=""` est **valide**. L'alias de ligne `primary` est accepté
  uniquement sous ce compte Paris, pas pour un autre emprunteur.
- `LoanInventorySnapshot` : résultat atomique avec `loans` positifs,
  `status=success|partial|failed|cancelled`, `observed_at` optionnel et code
  d'erreur dans un vocabulaire fermé. Enveloppe immuable, tuples et copies
  défensives des anciens objets `Loan` ; ne pas muter ces lignes après lecture.
- `LoanBranchCoverage` : couple `upstream`/`medium`, couverture
  `complete|partial|unknown`, fin de pagination explicite et code de cause.
  Exemple Paris : `dilicom/digital` et `syracuse/physical`, **indépendants**.
  BnF : `bnf_html/digital/partial`, même avec HTTP 200 et résultat vide.
- `branch_claims_complete(upstream, medium)` est **informatif** : ce n'est
  ni une preuve d'absence globale ni une autorisation de suppression de droit.
  Un snapshot `success` sans branches est **non exhaustif** ; une branche
  inconnue n'est jamais implicitement complète.

## Migration sans rupture

| Producteur | Lecture | Observations positives | Preuve négative |
| --- | --- | --- | --- |
| Plugin legacy v1 | `loans(account)` | conservées si même portée exacte | **jamais** ; statut `partial`, code `legacy_unverified` |
| Paris futur v1+ | `loan_inventory_snapshot(account)` opt-in | compte exact + branche attestée | **pas encore consommée par le Core** |
| BnF Prêt futur v1+ | idem | prêt positif attesté, SSO indépendant | **pas encore consommée** ; HTML incomplet |
| Erreur, fermeture, mix de comptes | `read_loan_inventory` | aucune ligne non vérifiée publiée | aucune, code fermé `unavailable/read_failed` |
| Annulation explicite `Cancelled` pendant une lecture v1 ou typée | `read_loan_inventory` | **aucune** ligne de lecture partielle publiée | aucune, `status=cancelled`, `reason_code=cancelled` |

La fonction `read_loan_inventory(session, account)` ne s'exécute **que sur
appel explicite** ; elle vérifie la connexion, la capability `loans`, le
couple provider/credential, puis utilise la méthode optionnelle ou l'adaptateur
legacy. Elle revalide compte/session **après** la lecture et écarte les résultats
si leur portée a changé ; une future intégration Core doit en plus confirmer
la génération et l'identité de la session enregistrée par le registre.
**Aucun GET n'est lancé par simple import ou introspection du contrat.**
En cas d'exception, ni son message ni l'état du compte ne sont publiés.
La cancellation Core/Plugin API ne doit pas être reclassée en `failed` : elle
produit un snapshot vide `cancelled`, sans lien vers un retour, une absence
d'emprunt ou une mutation. Les erreurs réseau et autres exceptions demeurent
`failed/read_failed`. Cette tranche de #3197 conserve le contrat v1 et ne
modifie aucun chemin fournisseur ou effet métier.


**Important :** la présence de méthodes Python ou de branches déclarées ne
vaut pas attestation d'autorité métier. Le Core doit encore revalider epoch,
identité et inventaire avant de persister. Le code de transition des droits
continue de relever de **#2975 P0** ; ce lot ne le modifie pas.

## Limites / prochaines tranches

1. **#3119 :** adoption contrôlée par les producteurs Paris puis BnF ;
   préciser explicitement la provenance de chaque ligne par branche, statut
   temporel, pagination et l'absence de preuves globales négatives.
2. **#3120 :** valider la capacité déclarée et sa disponibilité réelle.
3. **#3121 :** admission explicite des mutations et jetons ACSM.
4. **#3122 :** suites partagées Paris/BnF sur fixtures, installation/
   rollback et intégration prudente au Core. Aucune transition destructrice
   n'utilise actuellement `branch_claims_complete`.

Les tests de ce lot vérifient la forme, l'immutabilité de l'enveloppe,
l'isolement des comptes et identités, les branches indépendantes,
l'ancien `loans()`, les erreurs et la non-attestation des résultats vides.
Aucun accès réel fournisseur ni E2E GTK n'est revendiqué.
