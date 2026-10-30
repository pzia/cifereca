# Core P1 #3119 / #3122 — lecture atomique des prêts à l'ouverture

## Migration compatible

Le cycle d'ouverture utilise `read_loan_inventory(session, account)` pour les
providers qui exposent explicitement `loan_inventory_snapshot`. Tous les
providers v1 conservent `_loans_for_account(force=True)` et son comportement
historique. Il n'y a pas de deuxième lecture réseau et aucun changement aux
clients HTTP Paris/BnF.

Pour Paris, le statut atomique distingue les branches indépendantes sans
inférer leur exhaustivité ; pour BnF, même une réponse HTML vide demeure
`partial`. Le Core ne consulte plus `last_loan_errors`, y compris pour
les plugins v1 : leurs positifs restent compatibles, mais la couverture
est toujours non vérifiée. Paris a retiré ce champ en #3759.

## Révocation et commit

Avant et après la lecture, la Plugin API vérifie la portée exacte. Le Core
vérifie ensuite la session enregistrée, l'objet compte, l'identité,
l'activation et l'epoch. Il revalide aussi toutes les sources typées déjà
lues immédiatement avant la persistance de l'identité : le changement de
session pendant la lecture d'un compte frère invalide le lot au lieu de
publier des prêts sous l'ancien compte.

Un snapshot `failed`/`cancelled` est refusé avant stockage. Le résultat
`partial` conserve uniquement ses lignes positives et marque la source
partielle. Aucun `success` ni `complete` partiel ne vaut preuve d'absence
ou d'autorisation de retour.

Les observations positives typées sont publiées dans le cache de prêts
(`raw={}`) seulement **après** un commit de droits réussi. La
synchronisation manuelle suit désormais le même ordre pour éviter qu'un
lot refusé pollue les recherches ultérieures. Échec de mise en cache :
ne pas convertir un commit de droits valide en nouveau commit.

## Frontières

- Aucune mutation fournisseur ; pas de nouvel emprunt, retour, ACSM,
  ADEPT ni téléchargement automatique.
- L'acquisition historique à l'ouverture garde son garde explicite de
  capacité `opening_loan_auto_acquisition`. BnF reste interdit par défaut.
- Les autres chemins cache, droits, dashboard, retour/verif restent à
  qualifier ; #2975 reste propriétaire de la clôture.
- Tests synthétiques uniquement ; aucune recette réelle ni release.

## Tests

`tests/test_core_p1_3119_3122_opening_typed_inventory.py` couvre lecture
unique, Paris positif, BnF partiel et absence, évitement du canal mutable,
failed/cancelled, changement epoch/compte/session/désactivation, changement
d'un compte précédent lors de la lecture suivante, et rejet d'un cache
qui précéderait un commit invalidé.
