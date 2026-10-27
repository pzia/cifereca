# P0 #3120 — éligibilité d'un nouvel emprunt, scoped et sans effet de bord

Cette tranche complète **le diagnostic de capacité** de `lending_capabilities.py`.
La capacité `loan.borrow=supported`, la disponibilité de la notice
(`CanLoan=true`), la connexion et un résumé `account_status.usable`
**ne sont pas une autorisation d'emprunt pour un compte**.

`assess_borrower_eligibility(capability, account, notice, evidence)` est
une fonction **en lecture seule**, publiée dans `cifereca.plugin_api`.
Elle distingue :

- `unavailable` : capacité non déclarée/cohérente ou session non prête ;
- `unverified` : preuve absente, incompatible avec identité / provider /
  credential / emprunteur / docbase / collection / record_id, ou champ inconnu ;
- `blocked` : **preuve négative** sur le compte/opération exacts
  (`subscription_renewal_pending`, `subscription_inactive`,
  `borrower_quota_exhausted`, `borrower_permission_denied`,
  `item_offer_unavailable`) ;
- `eligible` : les **quatre états positifs ont été explicitement attestés
  par l'appelant pour cette notice, ce compte et cette branche**. Ce résultat
  **n'est ni un droit, ni un consentement, ni un jeton de mutation**.

## Contrat des observations

Le producteur Core ou un plugin fournisseur doit obtenir **séparément** et
**pour le compte/emprunteur/branche exacts** :

- `account_state = active | renewal_pending | inactive | unknown` ;
- `quota_state = available | exhausted | unknown` ;
- `permission = allowed | denied | unknown` ;
- `item_offer = available | unavailable | unknown`.

Le champ `unknown` est obligatoire en l'absence de donnée fournisseur
attestée ; il est interdit d'inférer un quota depuis un inventaire partiel,
un stock `CanLoan`, un livre voisin ou un résumé SYRACUSE du compte
principal quand l'opération vise DILICOM. Un statut de renouvellement
n'invalide **pas** la connexion, la consultation des prêts existants ou un
fichier déjà acquis. Les preuves périmées doivent être requalifiées avant
une décision ; un `BorrowerOperationEvidence` ne transporte aucune autorité
ou garantie de fraîcheur implicite.

La fonction ne déclenche **ni HTTPX, ni GET de permissions, ni synchronisation,
ni ADEPT, ni téléchargement, ni emprunt**. L'admission réelle demeure sous
les garde-fous `#3121` et le Core réexamine session/identité, droit attesté,
consentement et état distant avant mutation.

## Recette hors ligne

`tests/test_core_p0_3120_borrower_eligibility.py` :

- A : quota restant, renouvellement attendu → `blocked` sur A seulement ;
- B : abonnement actif, quota épuisé → `blocked` sur B seulement ;
- même titre mais édition / compte / collection / identité différents :
  jamais réutiliser un état ou un consentement ;
- compte primaire vide ≠ alias `primary` implicite ;
- absence de champ, valeur malformée, session ou capacité non prêtes :
  `unverified/unavailable`, jamais faux `eligible`.

**Limite de tranche** : pas de schéma inventé pour la réponse réelle Paris,
pas de collecte fournisseur, pas de raccordement autonome à l'UI ou à
`borrow_digital` sans preuve DILICOM et recette réelle. #3120 reste ouvert
pour cette intégration métier, la permission par opération et l'affichage.
