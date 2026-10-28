# #3171 — bail de session jusqu'au commit SourceReference

## Défaut reproduit

#3641 valide le compte upstream avant et après la lecture A, mais un autre
traitement peut encore modifier la session après cette lecture, pendant la
transaction `persist_attested_source_references`. Le registre Core et son
epoch peuvent rester identiques. Le garde de commit ne regardait alors que
ce registre : un lot positif pouvait être attribué à l'ancien borrower.

Les tests nouveaux échouent sur `main` a8dc313a (9 FAIL / 1 PASS) : les refus
attendus n'ont pas lieu. Ils injectent la bascule dans le trace callback d'un
vrai INSERT SQLite, après la lecture et l'admission du lot. Un droit existant
doit garder sa preuve antérieure et un deuxième droit ne doit pas être ajouté.

## Correction et frontières

`core/canonical_sync_stage.py::current_context` contrôle aussi le provider,
credential, l'unicité de la session et l'unique compte upstream de même portée
`identity/provider/credential/account`. Cette vérification utilise le même
callback avant les écritures et juste avant COMMIT, puis avant/après la
vérification B et avant la remise du snapshot à l'exécuteur.

Le callback ne rappelle pas Storage sous son verrou non récursif ; il ne fait
aucun GET, probe, acquisition ni écriture supplémentaire. Le getter `accounts`
du contrat de session est une projection en mémoire. Les comptes liés restent
permis s'il existe un seul compte correspondant à la portée sélectionnée.

Un snapshot déjà rejeté par le lecteur conserve son statut `failed` sans lot
positif ni bail de livraison : le contrôle initial conserve le contrat #3641.
Un changement du registre Core reste rejeté. Les annulations et exceptions
inattendues ne sont pas converties en succès.

Après COMMIT valide, un changement ultérieur de session interdit la remise
d'un snapshot périmé, sans effacer l'observation positive historiquement valide
au moment du commit. Pas de suppression de SourceReference, Artifact, Placement
ou OriginLink. Aucun changement Plugins, Diagnostics ou recette #3642.

## Preuves reproductibles

```sh
PYTHONPATH=src python -m pytest -q \
  tests/test_canonical_rights_commit_session_lease.py \
  tests/test_core_p0_3171*.py tests/test_core_p0_3196*.py
```

Dix nouveaux scénarios : sept bascules pendant SQL (compte absent, changé,
dupliqué, collection invalide, provider/credential modifiés, session dupliquée),
une bascule entre lecture et transaction, une bascule après contrôle B et un
succès avec comptes liés / deuxième Sync idempotente. Vraie SQLite v14, droits
fournisseur simulés, zéro requête de contenu ni fichier publié. Les trois
tests de refus préflight de #3641 restent inchangés et doivent rester verts.
La PR et #3171 consignent HEAD, résultats locaux et CI exacts.

Ce lot ne termine pas la migration #3196/#3197, la convergence d'ouverture ou
les recettes fournisseur/ADEPT réelles. #3171 ne doit pas être fermé sur ces
seules preuves synthétiques.
