# #3732 — rationalisation bornée des témoins Core/CoreUI

Référentiel de revue : `3dd6508e376ef04b07eb61f1aa0e59711bb707cb` (main,
après fusion #6627). Arbitrage du 17/11/2026 dans #6560 : traiter les
contrats retirés et les familles nommées, pas l'ensemble des tests historiques.
Le graphe et la campagne des six mutations restent la propriété de #5376.

## Familles nommées — disposition et contrat de remplacement

| Famille / fichiers examinés | Disposition | Contrat et témoins |
| --- | --- | --- |
| `test_kobo_backup_guard.py` | Conserver la barrière W2 ; supprimer uniquement la seconde définition strictement identique du témoin EPUB | `test_legacy_w2_never_downloads_or_mutates_hostile_local_candidates`, `test_retired_direct_executor_is_not_installed` interdisent le retour de l'exécuteur ; un seul `test_synthetic_kobo_epub_has_stable_bytes_across_zip_clock` reste collecté. Les tests d'atomicité canonique ne sont pas remplacés par des assertions d'absence. |
| `test_kobo_backup_observability.py`, `test_kobo_backup_reconcile_prune.py` | Conserver les refus historiques | `test_retired_w2_cannot_claim_progress_or_success`, `test_retired_w2_does_not_reconcile_or_prune_legacy_receipts` : pas de faux succès, ni réconciliation/suppression W2. |
| `test_kobo_backup_fault_isolation.py` | Compatibilité fixture uniquement | Aucun `def test_` ; les anciens tests W2 sont déjà retirés. Support synthétique encore importé, notamment par `test_source_materialization_confirmation.py`. Ne pas déplacer les fixtures globalement sans motif bloquant. |
| `test_kobo_backup_preview_targeted_connection.py` | Conserver sécurité/admission | Prévisualisation authentifiée : compte cible uniquement, refus source inconnue/autre identité/désactivée sans trafic. Une référence Kobo n'est pas en elle-même un dispatch Core actif. |
| `src/plugins/provider-kobo/tests/test_kobo_backup_manifest_binding_p1.py` | Conserver dans le plugin | Refus de GET et de publication sans liaison compte attestée ; contrat protocolaire du fournisseur. |
| `test_numilog_materialization_*` | Ancienne famille non retrouvée par recherche de nom ; vérifier liste exacte dans le checkout de recette | `test_core_p0_3171_numilog_v2_single_executor.py` : absence du worker historique et ABI canonique complète ; `test_core_p0_3719_six_v2_delivery_convergence.py` : six ressources, trois présences attestées, trois GET synthétiques puis zéro GET redondant. |
| `test_bnf_auto_materialization.py` | Ancien chemin absent au SHA de revue (404 sur le fichier exact) | `test_core_p0_3719_consent_legacy_dispatch.py` : exécuteur unique, ancien ON non autoritatif, révocation pendant livraison. `test_core_p0_3171_opening_convergence_executor.py` : ouverture et Sync explicite partagent le chemin A→B. |
| `test_core_p0_3171_source_auto_consent_bridge.py` | Conserver comportement canonique et témoins de migration | Source CAS obligatoire ; anciens ON lisibles mais non autoritatifs, OFF prioritaire, pas de propagation inter-type, état de droit optionnel ne bloque pas la livraison attestée. |
| `test_core_p0_3171_canonical_consent_broker.py` | Conserver contrôle exact du grant et de sa révocation | Isolation identité/édition, sélection invalide, rebind/compte dupliqué, pagination. Les grants exacts pris en charge ne constituent pas une obligation de consentir fichier par fichier en Sync. |
| `test_core_p0_3416_bnf_materialization_phase.py` | Conserver intégration de capacité réelle ET projection neutre | `test_generic_view_only_uses_declared_capability_and_does_not_overwrite_real_failure`, `test_loan_phase_declaration_accepts_future_provider_and_refuses_partial_abi` ; aucun routage par marque ne doit être inféré ; ABI append-only. |
| `test_core_p0_2565_reconciliation_phase_progress.py` | Conserver lecture neutre, ancien bouton manuel déjà retiré | `test_real_broker_emits_closed_progress_without_private_identifiers` (audit local). `test_core_p0_3185_reconciliation_compat_boundary.py` : outil de réconciliation absent et broker strictement lecture seule. |

Les deux motifs historiques non retrouvés ne sont pas comptés comme tests
supprimés par cette PR. Le fichier exact `tests/test_numilog_materialization.py`
a également retourné 404, ce qui ne démontre pas à lui seul l'absence de tout
fichier au préfixe. Vérification reproductible de la liste (à exécuter sur le
SHA de recette après les cutovers) :

```sh
git ls-tree -r --name-only HEAD -- tests src/plugins |
  grep -E '/test_(kobo_backup_.*|numilog_materialization_.*|bnf_auto_materialization|core_p0_3171_source_auto_consent_bridge|core_p0_3171_canonical_consent_broker|core_p0_3416_bnf_materialization_phase|core_p0_2565_reconciliation_phase_progress)\.py$'
```

## Matrice normative à rejouer dans la recette commune

| Invariant | Témoins existants vérifiés par lecture | Exécution à attester |
| --- | --- | --- |
| Provider inconnu / capacité explicite, aucune décision CoreUI par marque | `test_core_p0_3416_bnf_materialization_phase.py::test_loan_phase_declaration_accepts_future_provider_and_refuses_partial_abi` et `::test_generic_view_only_uses_declared_capability_and_does_not_overwrite_real_failure` | Core complet |
| Consentement source ON/OFF sans grant par fichier et ABA | `test_core_p0_3719_source_sync_consent.py::test_explicit_on_before_inventory_covers_new_resources_without_per_right_grants`, `::test_off_on_during_attempt_never_revives_old_generation`, `::test_policy_key_is_exact_for_every_source_coordinate` | Core complet |
| Présence incertaine, exécution commune et absence de GET redondant | `test_core_p0_3719_six_v2_delivery_convergence.py::test_six_v2_resources_three_attested_three_get_then_no_second_get`, `test_core_p0_3171_opening_convergence_executor.py::test_opening_consent_get_and_second_opening_are_idempotent` | Core, E2E applicables |
| Placement et déduplication après publication, échec advisory non bloquant, profil isolé | `test_core_p0_3719_post_placement_duplicates.py::test_postplacement_detects_only_same_profile_verified_copies`, `::test_probe_runs_after_commit_and_failure_cannot_unpublish`, `::test_tamper_symlink_missing_cannot_be_accepted` | Core complet |
| Writer historique non autorisé après activation typed | `test_core_p0_5975_w1_all_provider_typed_source_cutover.py::test_exact_typed_source_blocks_every_legacy_writer_without_mutation`, `::test_source_fence_matches_all_three_coordinates_only`, `::test_two_storage_instances_observe_committed_typed_source` | Core et mutation #5376 |
| Source exacte, diagnostics sans fuite, ancien écran absent | `test_core_p0_3171_canonical_consent_broker.py::test_scoped_token_cannot_enable_other_identity_or_edition`, `::test_rebind_or_duplicate_account_refuses_ui_consent`, `test_core_p0_2565_reconciliation_phase_progress.py::test_real_broker_emits_closed_progress_without_private_identifiers` | Core et E2E applicables |

## Preuves / limites / dette

- #6627 fusionnée `3dd6508e376ef04b07eb61f1aa0e59711bb707cb`, HEAD testé
  `2dd37a31e369ca8cc6c186b54a78cf659ef37fcf` : campagne des six mutations
  run [44232429320](https://github.com/pzia/cifereca/actions/runs/44232429320),
  PR CI [44232429275](https://github.com/pzia/cifereca/actions/runs/44232429275)
  SUCCESS. Cette preuve porte sur ce HEAD et non sur le futur HEAD de recette.
- Le nouveau manifeste `docs/test-classification-manifest.d/6560-contract-families.json`
  attribue les fichiers collectables effectivement lus. La fixture seule n'est
  pas un cas collecté. Les autres tests restent explicitement inconnus si leur
  classification n'est pas attestée.
- La duplication EPUB est littéralement identique (noms, corps, assertions) :
  Python n'exposait déjà que la seconde définition du symbole. La suppression
  ne revendique aucun gain de couverture ou de nombre de cas.
- Dette proposée P2, dans le backlog existant après la clôture : déplacer les
  helpers partagés encore logés dans les fichiers de tests historiques, réduire
  les matrices d'absence purement statiques après preuve comportementale
  équivalente et approfondir l'attribution sémantique hors familles bornées.
  Risque principal : maintenance des fixtures, non autorité métier ; ne pas
  reporter ainsi un import réellement bloquant, un faux vert ou un garde perdu.
- Aucun test local exécuté lors de cette revue. Les résultats de collecte,
  l'état détaillé de dernière Sync et la sélection E2E doivent encore être
  vérifiés dans la recette commune #5376/#3732 ; aucune clôture anticipée.
