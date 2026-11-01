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
