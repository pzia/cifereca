# Core P1 #3197 — refus des preuves négatives provenant de Paris v1

Le retour PNB et le parcours ADEPT-first utilisent `_read_paris_return_presence`.
Avant cette correction, une liste v1 vide, combinée à
`session.last_loan_errors=[]` (ou SYRACUSE seul), pouvait confirmer
un retour sans attestation atomique de fin de pagination DILICOM.

Désormais, un lecteur v1 conserve ses **observations positives** ; toute
absence v1 reçoit `return_inventory_legacy_incomplete` et reste
`verification_unavailable`. Ni une liste vide ni une erreur latérale
mutable ne suffisent. Seul un `LoanInventorySnapshot` typé `success`
déclarant la branche `dilicom/digital` complète et la pagination terminée
sur le compte exact peut attester l'absence, avec revalidation du scope.

Les tests d'absence auparavant fondés sur des listes factices doivent
opter explicitement pour une attestation **synthétique** de pagination.
Ceci n'atteste pas la vraie pagination du serveur Paris.

Aucun POST de retour supplémentaire, aucune activation ADEPT, aucun
changement de droit ni suppression de fichier. #3197 reste ouvert pour
décommission v1 complet, recette réelle et provenance des inventaires.
