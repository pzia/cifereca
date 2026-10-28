# Core P0 #3171 — lecture A sous bail d'emprunteur vivant

La projection positive `rights_observation_snapshot` est une lecture privée :
un compte inscrit dans `LibraryService.accounts` ne suffit pas si la session
fournisseur connectée a changé son emprunteur upstream sans remplacer l'objet
Python ou faire évoluer l'époque Core.

`core/rights_snapshot_read.py::still_bound` vérifie désormais exactement
un `Account` courant, du même quadruplet (identité, fournisseur,
credential, compte) que la requête, dans `session.accounts`. Les comptes
liés distincts restent autorisés dans la session : ils ne peuvent simplement
pas répondre à la place du compte sélectionné.

Ce contrôle est effectué **avant** l'appel privé du producteur et **après**
son retour, avant toute admission de références A. Un compte absent,
dupliqué, remplacé ou une collection invalide ferme la lecture sans
publier de droits. Les erreurs de récupération de `session.accounts`
échouent fermées sans relayer d'exception fournisseur brute.

## Recette sans fournisseur

- `tests/test_core_p0_3171_rights_snapshot_read.py` : cinq variantes
  de refus avant lecture (étranger, manquant, doublonné, invalide,
  inaccessible), bascule pendant la lecture, et compte lié non bloquant.
- Les fixtures d'ouverture Core reflètent explicitement le compte upstream
  de la session.
- Validations CI Core/Plugins et installation complète requises avant fusion.

Aucun GET de livre, ADEPT ou fournisseur réel. Cette tranche complète
les gardes d'offre, GET candidat et B de #3636 ; #3171 ne peut pas être
fermé sans la recette d'acquisition réelle et ses preuves de reprise.
