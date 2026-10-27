# Core P1 #3185 — relocalisation sans promotion de propriétaire legacy

`Storage.relocate_local_path(old, new, identity_id=..., provider_id=...)`
mettait historiquement à jour les anciens `backup_items` en exécutant
`identity_id=COALESCE(identity_id, identity_id_appelant)`. Quand
`old==new`, les gardes de déplacement ne s'appliquent pas (aucun chemin
n'est modifié) mais **cette écriture pouvait néanmoins transformer un
propriétaire inconnu (NULL) en propriétaire attesté**.

Le chemin de compatibilité n'attribue désormais **jamais** d'identité :
une véritable relocalisation ne modifie que `local_path` des lignes
`backup_items` ayant déjà exactement `identity_id` et `provider_id`
demandés. Un appel idempotent avec `old==new` ne modifie aucune ligne
legacy. La garde existante refuse un chemin de déclaration appartenant
à une autre identité ou d'identité inconnue avant mutation.

Recette SQLite synthétique : no-op avec identité de projection manquante
(elle reste NULL), vrai déplacement d'une projection sans owner (refus
atomique), vrai renommage déjà scoppé (chemin actualisé, identité stable).
Aucun déplacement physique n'est initié dans ce lot.

Ceci ne résout pas l'ensemble de #3185 : checkpoints d'acquisition,
preuves historiques, changements de dossiers et variantes nécessitent
toujours une politique de migration spécifique. Le Core n'infère
aucune propriété depuis le SHA, un titre ou une présence sur disque.
