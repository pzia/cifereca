# P0 #3185 — adoption conservatrice des Placements historiques (tranche 1)

## Objectif

`core.canonical_legacy_adoption.adopt_verified_legacy_placement` fournit un
aperçu puis une migration **non destructive**, séparés du superviseur de
dédoublonnage canonique. Aucun fournisseur, ADEPT, droit, fichier, chemin,
OriginLink, SourceReference, catalogue, backup ou reçu n'est créé/supprimé/
déplacé par cette tranche. Seules les deux anciennes colonnes
`placements.provider_id/source_item_id` sont mises à `NULL` après preuve.

### Admission

- Un même Artifact et un même profil exact pour le candidat historique
  et le keeper canonique ; deux chemins physiques distincts.
- Deux fichiers réguliers non symlink vérifiés par SHA-256 et taille sous
  le répertoire déclaré unique du profil, avec contrôle d'inode.
- Preuve d'origine publiée, SourceKey et compte/credential exacts,
  connexion active non ambiguë et un seul reçu terminé portant **ce chemin**.
  Toute deuxième entrée ledger mentionnant le chemin est refusée.
- Inventaire borné de l'ensemble des métadonnées opérationnelles :
  `catalog_entries`, `backup_items`, `artifact_variants`,
  `entitlements/artifacts/placements.metadata_json`, `metadata`,
  déclarations, ainsi que la totalité des reçus via la preuve stricte déjà
  existante. Un budget dépassé, une structure illisible, une référence
  étrangère/opaque ou un chemin encodé et non qualifiable refusent.
- La preuve est **réévaluée au commit** sous le verrou global d'acquisition
  réellement détenu, `confirmed=True` et transaction `BEGIN IMMEDIATE`.
  La vérification physique est refaite avant la mutation puis après, et
  SQLite annule l'UPDATE si une condition manque.

### Utilisation Core interne

```python
preview = adopt_verified_legacy_placement(
    storage, library_root=root, candidate_placement_id=old_id,
    keeper_placement_id=canonical_id,
)
# status == review_required : diagnostic, PAS autorisation persistante.
with InterProcessLock(acquisition_lock_file()) as lock:
    result = adopt_verified_legacy_placement(
        storage, library_root=root, candidate_placement_id=old_id,
        keeper_placement_id=canonical_id, action="commit",
        confirmed=True, shared_lock=lock,
    )
```

Cette primitive reste **Core interne** : aucun déclenchement automatique
depuis Sync, plugin ou UI tant que l'admission opérateur et le contrat public
ne sont pas livrés. Un résultat `migrated` veut seulement dire que
l'ancien libellé fournisseur de ce Placement a été effacé atomiquement ;
les deux chemins et tous les reçus demeurent intacts. Le second commit
signale `already_canonical` sans mutation. Aucun résultat ne confère un
droit de suppression (`may_delete=False`).

La fusion éventuelle de deux fichiers exige une **nouvelle** action explicite
auprès de `supervise_canonical_duplicate`, avec son propre plan, verrou,
rehash, contrôle des références, tombstone et reprise. L'adoption ne valide
pas ce plan et n'efface jamais un checkpoint consultatif.

## Limitations et sortie du P0

La tranche 1 ne change pas les liens opérationnels ; elle conserve leur
chemin historique tant que le fichier physique existe. Les cas dont le
contrat de référence ne peut être classé sont `blocked`, pas devinés depuis
une égalité SHA. Un broker public et l'UX de confirmation pourront être
raccordés uniquement après validation de la CI et de la matrice de conflits.
Les scénarios sont entièrement synthétiques SQLite/fichiers. Aucun livre
utilisateur n'est modifié.
