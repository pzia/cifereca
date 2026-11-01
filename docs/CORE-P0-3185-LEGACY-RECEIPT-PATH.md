# P0 #3185 — preuve de chemin par reçu canonique terminé (lecture seule)

`review_legacy_placement_scope` inspecte désormais, **seulement après rehash
physique des deux copies du même profil**, les reçus canoniques
`acquisition_ledger` de la source exacte (fournisseur, édition, credential,
compte, branche, SourceKey et ID d'opération). Un reçu candidat doit être
`complete`, porter exactement le chemin et SHA du Placement historique,
avoir un identifiant d'offre et un reçu valide, et retrouver son
`OriginLink(provider)` publié sur le même Artifact et la même identité.

- Un seul reçu strictement conforme retourne le diagnostic
  `completed_receipt_path_review_required` avec
  `completed_path_receipts=1`. Le statut **reste `blocked`**.
- Reçu absent : `legacy_file_source_unbound`.
- Reçu incomplet, collision, checksum contradictoire, origine non publiée,
  métadonnée invalide ou ambiguïté : `legacy_receipt_unverified`.
- Un compte fournisseur unique sur l'Artifact ne prouve toujours jamais à
  lui seul la propriété **d'un chemin historique**.

Il s'agit d'un **indice vérifiable**, pas d'une adoption automatique.
`may_migrate_or_retire` reste toujours `False` : aucun `DELETE`, relink,
`unlink`, autorisation d'emprunt ou source de droit. La migration historique
nécessite encore une preuve d'ensemble de toutes les références
opérationnelles, un plan Core dédié et une confirmation explicite sous verrou.

Le lecteur est borné à 1 000 reçus pour la source examinée. Aucun chemin,
compte, titre, identifiant privé ou contenu de reçu n'est publié au broker.
Tests SQLite/PDF entièrement synthétiques.

## Exhaustivité négative : collision de chemin dans le ledger

Un reçu positif *de la source attendue* ne suffit pas à prouver que le chemin
historique n'a jamais été enregistré pour un autre compte ou fournisseur.
Avant de retourner `completed_receipt_path_review_required`, le diagnostic
examine donc **tous les reçus** dans un snapshot SQLite cohérent (budget
100 000). Une deuxième entrée, même `retryable_error`, qui cite le même
chemin (directement, sous une propriété imbriquée ou avec encodage JSON)
annule cette indication : `legacy_receipt_unverified`. Un JSON ambigu,
illisible ou un inventaire tronqué n'est jamais interprété comme une absence.

Les coordonnées de connexion et compte sont aussi revérifiées dans
`provider_connections` et `provider_accounts`, ainsi que
`branch_id/right_id/reference_kind` dans la SourceReference liée à
l'OriginLink. Cette lecture n'autorise toujours ni migration, ni effacement.
