# Core P0 #3196 — D2 : un checkpoint 0.7 ne compte plus comme livraison

## Changement effectif

La confirmation Kobo historique (`core/source_materialization.py::execute`)
ne calcule plus le nombre de téléchargements réussis par différence entre
deux inventaires `backup_items.complete` (statut, chemin, SHA, révision).
Cette heuristique permettait à une réécriture de la projection v0.7,
y compris un pointage vers un fichier inexistant, d'augmenter
`changed` sans réception réelle d'un fichier.

Le nombre de livraisons annoncé provient désormais uniquement :
- du signal **privé** `on_delivery_committed` émis par l'exécuteur
  immédiatement après l'inspection physique du fichier et la persistance
  du résultat d'acquisition, avant le checkpoint source ; il permet de ne
  pas perdre une livraison réelle lorsque ce checkpoint échoue ;
- des événements publics `downloaded`, qui demeurent différés jusqu'à
  la réussite du checkpoint et ne peuvent pas être anticipés ;
- du champ `downloaded` du rapport **Core** validé lors d'un retour normal.

Le résultat n'est pas inféré depuis une relecture de la table historique :
le signal privé provient de l'exécution effective après réception des octets.
Il reste interdit de publier un événement public `downloaded` avant
l'enregistrement réussi du checkpoint source.

Si l'exécution s'interrompt avant de rendre un rapport final, aucune
ligne `backup_items` ne vient fabriquer une livraison supplémentaire ;
`completion_unknown` reste indiqué et la reprise reste conditionnée à
une nouvelle confirmation de la source. L'archive 0.7, les fichiers
présents et l'état de reprise ne sont ni effacés ni modifiés par ce
changement. Le préflight continue à examiner `backup_items` pour
**refuser** les chemins douteux : cela n'est pas une preuve positive
de droit ni d'acquisition.

## Limites de sortie

C'est une coupure de l'autorité positive **D2 de comptage**, pas le
retrait de W2 : le téléchargeur legacy utilise encore `backup_put`
pour ses checkpoints, et la confirmation ne bascule pas une ancienne
offre vers le pipeline canonique. Aucune preuve B n'est créée à partir
de l'historique, aucun GET supplémentaire n'est autorisé.

Réception : `tests/test_source_materialization_confirmation.py`,
avec checkpoints complets trompeurs, interruption et compatibilité
des vrais rapports/événements.
