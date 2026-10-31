# P0 #3185 — Contrat de dédoublonnage canonique Core v14

**État :** primitives Core `#3171/#3719/#3185` intégrées. Le raccordement au broker public et au plugin Maintenance est proposé séparément par **#3820/#3821** (ne pas considérer ces PR comme intégrées avant leur fusion).

## Autorité et invariants

- **Artifact** : SHA-256 global et taille vérifiés sur les deux fichiers physiques. L'identité d'un Artifact n'est **pas** un droit ni une preuve de provenance.
- **Placement** : copie physique, chemin et identité. Une consolidation reste **dans le même profil** ; aucun dédoublonnage interprofils.
- **SourceReference / OriginLink** : chaque fournisseur, compte, droit descriptif, reçu et origine est indépendant ; ne jamais en déduire un droit à partir d'octets identiques.
- **Métadonnées** : les reliaisons vérifiées concernent les références catalogue, `backup_items` et reçus d'acquisition terminés. Les déclarations, variantes, métadonnées opaques ou checkpoints intermédiaires qui mentionnent encore l'ancien chemin bloquent la suppression physique.
- La **Sync/acquisition ne déclenche jamais** un retrait physique automatique. Le programme de maintenance ne peut exécuter que des opérations confirmées explicitement.

## Pipeline Core unique

`canonical_dedup_preview.preview_canonical_duplicate` : inspection **lecture seule** du candidat et du keeper, SHA/taille/inode réguliers, racine/profil, références actives. Les états `unreferenced` et `active_references` sont des **diagnostics**, pas une autorisation. L'aperçu fournit aussi `artifact_origin_links`, `linked_source_references` et `provenance_counted` après rehash réussi des **deux copies du même profil**. Ces compteurs concernent l'Artifact + l'identité, **jamais la provenance d'un chemin précis, ni un transfert de droits par SHA**. En cas d'écart de profil, de fichier altéré ou de contrôle impossible, `provenance_counted=False` interdit d'interpréter les zéros comme absence d'origine.

`canonical_dedup_prepare.prepare_verified_canonical_duplicate` : relie les entrées catalogue, `backup_items`, puis les reçus terminés attestés. Les transactions SQLite sont indépendantes, idempotentes et réexécutables ; un refus intermédiaire laisse les deux fichiers présents. Aucun unlink dans cette étape.

`canonical_dedup_supervisor.supervise_canonical_duplicate` est le **seul exécuteur canonique**. Il expose `action='preview'|'commit'|'recover'` (défaut : `preview`). Les actions `commit` et `recover` exigent **`confirmed=True` et le verrou d'acquisition partagé réellement détenu**. Elles revérifient le profil, les octets et les références sous le verrou ; une confirmation de plan ancien n'autorise jamais à sauter les contrôles.

`canonical_dedup_retirement.retire_verified_canonical_duplicate` : crée le tombstone `dedup_cleanup_pending` avec chemin, keeper et signature d'inode **avant tout unlink**. Après effacement vérifié, le répertoire est fsync, puis le tombstone est supprimé. Le chemin redevient disponible pour un autre livre. Le postcheck Core exige un keeper rehashé et l'absence de chemin physique candidat.

`canonical_dedup_retirement.recover_canonical_duplicate_retirement` : reprise explicite et idempotente à partir d'un tombstone, sous le même verrou. Un inode remplacé, un lien symbolique, un nouveau lien actif ou un keeper modifié empêche l'effacement. Une panne après unlink peut être finalisée sans ré-effacer un nouveau fichier.

## Intégration Maintenance (une fois #3820 fusionnée)

Le broker `library.maintenance` expose `LibraryMutationRequest(operation='canonical_dedup')` :

```python
from cifereca.plugin_api import LibraryMutationRequest

# Toujours read-only : aucun verrou ni nouveau droit nécessaire.
request = LibraryMutationRequest(
    "canonical_dedup",
    placement_id=candidate_id,
    keep_placement_id=keeper_id,
)
preview = broker.run(broker.plan(request))
# Lire preview.facts["status"] et preview.facts["reason"], sans considérer
# ces informations comme une permission de supprimer.

# UNIQUEMENT après confirmation humaine d'une action destructive.
request = LibraryMutationRequest(
    "canonical_dedup",
    placement_id=candidate_id,
    keep_placement_id=keeper_id,
    options={"action": "commit", "confirmed": True},
)
result = broker.run(broker.plan(request))
# Si result.facts["status"] == "cleanup_pending", ne pas relancer commit.
# Demander une NOUVELLE confirmation explicite pour l'action recover.

request = LibraryMutationRequest(
    "canonical_dedup",
    placement_id=candidate_id,
    keep_placement_id=keeper_id,
    options={"action": "recover", "confirmed": True},
)
recovery = broker.run(broker.plan(request))
```

La racine est exclusivement celle de `service.config.get('library.directory')`; un plugin ne peut pas fournir son propre chemin racine. Le résultat expose seulement `status`, `reason`, compteurs de reliaison et indicateur d'effacement, **jamais** les chemins, comptes, jetons ou justificatifs.

L'opération historique `delete_duplicate` reste réservée aux Placements non canoniques avec contrat de portée legacy. Elle **ne peut pas** supprimer un Placement B `provider_id IS NULL AND source_item_id IS NULL`. L'outil GTK `library.duplicates.cleanup` doit utiliser l'API supervisée après son dialogue destructif (raccordement #3821).

## Inventaire des avis de doublon post-placement (PR #3980)

Les checkpoints consultatifs `core.canonical-dedup-pending.v1.*` sont une **file de diagnostic**, pas des tombstones et encore moins une autorisation de supprimer. Compter les **enregistrements distincts**, jamais le nombre de lignes de log `canonical_dedup_pending` qui ont pu être répétées.

API Core (lecture seule) : `review_canonical_dedup_advisories(storage, identity_id=...)`, accessible à la maintenance par `LibraryMaintenanceBrokerImpl.pending_canonical_advisories(identity_id=...)`. Le snapshot SQLite applique des limites fermées (250 checkpoints / 64 Kio de JSON par checkpoint), vérifie l'identité, l'Artifact et la portée de chaque Placement. Il ne renvoie aucun chemin privé.

- `pending_preview` / `physical_review_required` : enregistrements SQLite cohérents **mais octets NON vérifiés** par ce lecteur ; demander ensuite un `preview` Core frais. Cela ne constitue pas une autorisation.
- `blocked` : checkpoint invalide, changement de Placement, propriétaire incohérent, Artifact absent ou retrait en cours ; conserver le marqueur et examiner.
- `partial`, `over_budget`, `unavailable` : absence de preuve d'inventaire complet ; ne jamais convertir en « zéro doublon ».

Un checkpoint de **paire exacte** est acquitté seulement à la fin d'un retrait supervisé entièrement vérifié (#3976), dans la transaction de finalisation de la tombstone, après effacement, `fsync` et contrôle du keeper. En présence d'une troisième copie, d'un marqueur inconnu ou d'une interruption, le marqueur reste pour examen. Les changements d'origine et de droits sont interdits.

## Points de reprise et matrice de refus

| Frontière | État conservé | Action autorisée |
| --- | --- | --- |
| Avant la première transaction | Deux Placements `present`, deux fichiers | Recommencer `preview` |
| Reliaison catalogue effectuée, backup/ledger en échec | Deux fichiers, références SQL partiellement reliées et sûres | Recommencer `commit` après inspection/confirmation |
| Tombstone SQLite créé, unlink refusé | Placement `dedup_cleanup_pending`, chemin et inode originaux | `recover` explicite confirmé |
| Unlink réussi, fsync ou finalisation SQL refusé | Tombstone conservé ; absence physique à revalider | `recover` explicite confirmé |
| Copie remplacée, keeper altéré, autre profil, références inconnues | Refus, aucun unlink supplémentaire | Résoudre la cause, ne pas forcer |
| Retrait et postcheck complets | Keeper vérifié, candidat SQL + filesystem absent | Terminé, aucune reprise nécessaire |

## Critères de recette et interdictions

1. Scénarios synthétiques : deux fournisseurs dans A, mêmes octets chez B, trois familles de références à relier, compteurs et OriginLinks inchangés, refus interprofils.
2. Tests de rollback : échec SQL pendant backup/ledger, crash entre SQLite/FS, refus `unlink`, anomalie de `fsync`, récupération, chemin réutilisé par un autre livre, scan concurrent, transfert local et migration de répertoire pendant tombstone.
3. **Aucune recette réelle de prêts/droits DRM** ne peut être remplacée par des fixtures synthétiques. Vérifier les ouvrages réels sous contrôle de l'utilisateur et conserver leurs reçus/provenances.
4. Ne pas relier directement une GUI ou Sync aux primitives privées de retrait ; passer par le superviseur et l'admission maintenance. Ne pas supprimer automatiquement les copies restantes à la suite d'un GET, d'un achat, d'une restauration ou d'une égalité SHA.
5. La CI PR complète et l'installation Core doivent être vertes sur le **HEAD exact** pour chaque PR Core avant fusion. Aucune release ne découle du dédoublonnage.
