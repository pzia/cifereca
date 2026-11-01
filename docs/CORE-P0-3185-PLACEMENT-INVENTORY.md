# P0 #3185 — inventaire Core des Placements indépendamment du catalogue

La fonction `read_canonical_placement_inventory(storage, identity_id=...)` et le
broker `library.maintenance.canonical_placement_inventory(identity_id=...)`
énumèrent **tous les Placements SQLite v14 enregistrés pour une identité**,
qu'ils possèdent ou non une fiche `catalog_entries`. Les groupes sont par
Artifact exact et identifiant de profil, pas par titre, fournisseur ni chemin.

- `candidate_review` : au moins deux Placements `present` canoniques sur
  le même Artifact dans une même identité. **Aucun octet vérifié** ; le
  superviseur Core doit encore contrôler fichiers, SHA, inode, propriétaire,
  `OriginLink`, reçus et références sous verrou après confirmation explicite.
- `blocked` : Artifact absent/incohérent, Placement non présent, liaison
  catalogue d'un autre profil/Artifact, source legacy attachée au Placement.
  Ne jamais appliquer un retrait.
- `unique` : un seul Placement enregistré, sans preuve d'intégrité physique.
- `partial / over_budget / unavailable` : la réponse ne prouve aucune
  exhaustivité utilisable pour autoriser un nettoyage.

Lecture seule, bornée à 5 000 Placements par identité, sans parcours disque ni
appel fournisseur. Les chemins, comptes et jetons ne sortent pas du Core. Les
Placements non catalogués ne sont plus invisibles aux vues de maintenance.

Cette API est **préparatoire** : elle ne réactive pas les tools suspendus par
#3998, ne remplace pas la supervision `canonical_dedup`, n'effectue ni
`DELETE`, ni `unlink`, ni rattachement d'origine.
