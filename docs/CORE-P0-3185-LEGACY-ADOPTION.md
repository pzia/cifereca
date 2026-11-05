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
# Le jeton lie la validation aux inodes des deux fichiers ET du profil.
with InterProcessLock(acquisition_lock_file()) as lock:
    result = adopt_verified_legacy_placement(
        storage, library_root=root, candidate_placement_id=old_id,
        keeper_placement_id=canonical_id, action="commit",
        confirmed=True, shared_lock=lock,
        expected_review_token=preview.review_token,
    )
```

La primitive est raccordée au broker public `library.maintenance`
via `LibraryMutationRequest(operation="legacy_adopt")`, avec
`placement_id`, `keep_placement_id`, et
`options={"action": "preview" | "commit", "confirmed": True}` pour le commit.
Le broker refuse toute action non prévue et tout plan forgé, reprend
l'aperçu à l'exécution sous le verrou d'acquisition, et conserve dans le plan
un jeton lié à la paire d'inodes, aux répertoires racine/profil et aux
preuves SQL. Un échange de répertoire entre plan et commit refuse
`stale_plan` même lorsque les octets et SHA restent identiques.
Ce jeton n'est jamais une permission de mutation à lui seul.
Le broker expose seulement
des compteurs et codes fermés (jamais de chemin ni reçu). Son plan
`commit` est marqué `destructive=True` pour requérir une confirmation
d'opérateur, **mais aucun octet physique n'est détruit**. Le plugin de
maintenance et l'UI restent à raccorder explicitement : aucune Sync ne
déclenche cette opération. Un résultat `migrated` veut seulement dire que
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
une égalité SHA. L'UX ou le plugin Maintenance doivent encore présenter cette action à
l'opérateur ; un appel programmatique au broker exige déjà confirmation
explicite et verrou, sans supposer qu'une prévisualisation persiste.
Les scénarios sont entièrement synthétiques SQLite/fichiers. Aucun livre
utilisateur n'est modifié.
