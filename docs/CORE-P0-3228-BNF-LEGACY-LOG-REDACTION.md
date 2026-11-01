# P0 #3228 + #2888 — journal BnF historique : protéger les coordonnées de compte

## Arbitrage et frontière entre diagnostics

Dans le chemin de compatibilité `bnf_auto_materialization`, huit logs
interpolent initialement `identity_id`, `credential_id`,
`account_id` et `loan_id`, et six exceptions possèdent un
`exc_info=True`. Les coordonnées de profil/connexion n'apportent
aucune indication supplémentaire sur la phase qui a échoué.

**Respecter le contrat de fidélité du journal brut privé #2888** :
l'identifiant du prêt observé (s'il existe), le fournisseur canonique,
la phase réellement exécutée et **l'exception d'origine** restent
disponibles au journal brut local. La suppression de l'exception (envisagée
dans une première version de cette PR) masquerait le diagnostic
P0/P1 et casserait les tests contractuels #2888. Ce n'est pas le produit
retenu.

**Réduction de données** : les huit templates du worker utilisent
seulement `provider_id`, éventuellement `loan_id`, et le nom de
phase fixe. Aucun `identity_id`, `credential_id` ni `account_id`
n'est interpolé. Les exceptions peuvent contenir des valeurs sensibles
et restent de ce fait des **données du journal brut privé**, jamais
des preuves publiables dans une PR, un ticket ou un partage non relu.
La projection des valeurs identifiables suit toujours
`diagnostic_acquisition_failure.protect_text` : elle ne garantit pas
l'anonymisation des chemins ou des chaînes inconnues, ni la
désidentification complète d'une archive brute.

Le classificateur reconnaît les nouveaux templates à deux paramètres,
ainsi que le contrôle de reçu à un seul `provider_id`, tout en
conservant les anciens templates pour consultation des journaux
antérieurs. Aucun changement de statut, consentement, GET, ADEPT
ou autorisation. Les tests de #2888 conservent la cause d'origine
et le `loan_id`, tout en refusant les trois IDs de profil/compte.
Le nouveau test AST verrouille les huit messages du worker.

## Hors périmètre

Ce correctif ne remplace pas la suppression du worker BnF historique
(#3228), ni les preuves d'ACSM/ADEPT, ni la recette du parcours
d'emprunt manuel #2055. La confidentialité des ZIP privés non relus
reste une contrainte de traitement : ne pas les publier dans GitHub.
