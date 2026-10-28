# #2847 → #3638 : faits Core privés par Placement

## Livraison A et frontière avec C

L'audit Core existant possède déjà le snapshot SQLite et les résultats du probe
`inspect_stored_paths`. C recevait seulement `LibraryAudit.redacted()` : les
identifiants par ligne ne pouvaient pas être reconstruits dans Diagnostics.

Le broker conserve maintenant une référence éphémère à cet audit terminé,
revalide portée et fraîcheur SQLite **avant et après** la projection, puis
ajoute `placement_observations` à une **copie privée** de l'argument de
`Diagnostics.add_core_reconciliation_result`. Le retour public du broker reste
strictement agrégé ; ni Inventory, ni snapshot, ni session LocalInspection ne
sont passés à Diagnostics. Aucune mutation, aucun nouveau probe/SQL/scan.

**C garde son composant et son intégration.** Son consommateur actuel ignore
ce nouveau champ. Cette PR A rend les faits disponibles, sans prétendre les
avoir journalisés/recherchés/exportés. Aucun fichier `diagnostic_*`, logger,
IHM ou Plugin n'est modifié. #3638 reste ouvert jusqu'à preuve de réception C.

## Enveloppe `cifereca.placement.observation/1`

| Champ | Signification et autorité |
| --- | --- |
| `observation_ref` | Identifiant aléatoire de cette occurrence de remise ; ni ID métier, ni droit, ni hash de contenu |
| `identity_id` | Profil effectivement inspecté et revalidé ; jamais profil reconstruit du chemin |
| `scope_validated`, `read_only` | Vrais uniquement dans la remise Core validée ; pas des jetons de mutation |
| `repair_authorized`, `acquisition_authorized` | Toujours faux |
| `probe_coverage`, `inventory_coverage` | Couvertures indépendantes `complete/partial` des phases existantes |
| `reasons` | Raisons Core de la phase de chemins ; aucun texte d'exception arbitraire ajouté |
| `total_rows`, `emitted_rows`, `truncated` | Lignes du profil, lignes remises et borne explicite (256). Une ligne non remise n'est pas déclarée absente |
| `observations` | Liste bornée décrite ci-dessous, sans métadonnées SQLite libres ni données de licence |

La corrélation de worker est celle de l'entrée Diagnostics existante ; ce lot
n'invente pas de task_ref depuis un chemin, profil ou horodatage. Les stamps
stat privés de SQLite ne sont ni sérialisés ni exposés. Les probes restent des
observations dans la fenêtre de l'audit : pas de fraîcheur de fichier garantie
pour une future réparation et pas d'heure précise inventée.

Chaque observation porte :

- `placement_id`, `artifact_id` : valeurs enregistrées dans la ligne SQLite,
  uniquement si présentes/valides. Absence conservée, aucune reconstruction par
  chemin/SHA/titre/ISBN. Un ID Artifact enregistré ne prouve pas ses octets.
- `path` : chemin enregistré. Les valeurs explicitement marquées comme secrets
  sont protégées via le helper existant de C ; `path_credentials_removed=true`
  signale ce cas. Un chemin protégé ne peut pas servir de cible d'action.
- `recorded_provider_id`, `recorded_source_item_id` : labels enregistrés,
  **pas** nouvelles attestations de compte, droit ou provenance fournisseur.
- `sqlite_state` : ancien état enregistré, distinct du résultat physique.
- `state=present|missing|unavailable|unverified`, `observation_source=core_stored_paths`.
- `reason` : motif effectivement conservé au probe, suivant la matrice.
- `restoration=not_evaluated` : absence de décision de restauration. Une copie
  ponctuelle/transfert n'est jamais promue en droit fournisseur.

## Motifs conservés sans refaire les probes

| Résultat | Motif observé | Ce qu'il ne prouve pas |
| --- | --- | --- |
| `present` | `regular_file_observed` | Droit, licence ou SHA final valide |
| `missing` | `enoent_observed` après les vérifications existantes | Suppression du livre/droit, autorisation de nettoyage |
| `unavailable` | `root_missing` | Cause du root absent : aucune table de montage consultée |
| `unavailable` | `root_permission_denied` ou `permission_denied` | Absence du fichier |
| `unavailable` | `root_changed`, `path_changed` | État physique stable après l'audit |
| `unavailable` | `symlink_or_not_directory`, `not_regular_file`, `invalid_path`, `io_unavailable`, `root_unavailable`, `probe_unavailable` | Suppression ou restauration autorisée |
| `unverified` | `out_of_scope` ou `not_examined` | Présence/absence pour ce profil |

`StoredPathReport.observation_reasons` conserve seulement ces codes au même
index privé que `states`. Les décisions de probe, les compteurs publics et les
garanties no-follow existantes restent identiques. Aucune nouvelle inspection
de montage, permission ou fichier n'est déclenchée pour enrichir le diagnostic.

## Contrôles et limites

`tests/test_core_placement_observation_handoff.py` consomme le vrai broker et
ses probes sur SQLite/EPUB synthétiques : IDs exacts, profil étranger exclu,
root indisponible/permissions, budget partiel avec ligne non examinée,
invalidation du profil/source après finish et pendant projection, borne
explicite, absence d'I/O dans le projecteur, base/fichiers inchangés, secrets
du chemin protégés et résultat public isolé du consommateur privé.

```sh
PYTHONPATH=src python -m pytest -q \
  tests/test_core_placement_observation_handoff.py \
  tests/test_library_audit_broker.py tests/test_library_stored_paths.py \
  tests/test_core_p0_2730_reconciliation_diagnostics.py \
  tests/test_diagnostics_missing_placements.py \
  tests/test_core_p0_2847*.py tests/test_library_local_inspection*.py
```

Les preuves exactes sont consignées dans la PR et #2847. Ce ne sont pas des
preuves de recette fournisseur, de réparation #2847/#3185, de parcours GTK ou
de copie réelle #3607/#3642. Aucun de ces tickets parents n'est clos par A ici.
Après fusion, C peut consommer l'enveloppe, protéger/valider les champs de son
journal et prouver leur fidélité sur toutes ses surfaces.
