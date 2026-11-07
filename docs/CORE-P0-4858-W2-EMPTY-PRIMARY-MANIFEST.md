# P0 #4858 — W2 : emprunteur Kobo primaire vide, session exacte

## Résiduel

Le moteur historique W2 épinglait `identity/provider/credential/unique_id`
du compte de session, mais pas `is_primary`. Or `unique_id=""` n'est
un emprunteur valide que pour le compte primaire Kobo. Une mutation in
place du même objet `Account` ou une session secondaire vide avant
lecture du manifeste pouvait conserver les quatre coordonnées, puis
autoriser le manifeste et l'exécuteur 0.7.

## Changement

`manifest_source_current` capture aussi `is_primary` et le revérifie
avant et après le manifeste, ainsi qu'avant tout GET et writer via le
contrat existant. Pour `unique_id=""`, exige explicitement
`is_primary is True`. Les emprunteurs non vides, l'identité du compte,
le registre de session, le CAS canonique et les contrôles de connexion
sont conservés.

Tests : `tests/test_core_p0_3196_w2_manifest_source_rebind.py`
couvre compte secondaire vide avant manifeste, substitution pendant
manifeste, mutation `is_primary` sur le même objet, et manifeste vide
d'un vrai primaire Kobo ; aucune acquisition ni promotion SQL lors
des refus.

Ce verrou est une barrière W2 négative ; il ne retire pas l'exécuteur
legacy sur ABI partielle et ne clôture pas #4858/#3196.
