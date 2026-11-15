# P0 #5975 — Revue ciblée : identité ADEPT et observation des retours (15/11/2026)

**Objet : qualification de cinq sites AST déjà exécutables, sans changement de code métier ni retraite de garde.** Référence : `docs/provider-business-coupling-review.json`, inventaire `core.provider.business-coupling/1`, baseline de la PR sur `main` `bb25021ef917c11815cd1edac39c54bd5a1d9f45`.

## Contrats vérifiés

| Site stable | Classification | Preuve et autorité | Remplacement avant retrait |
| --- | --- | --- | --- |
| `site-7874908d12d351ccb80742b1` — `core/adept_borrower.py` | `safety_guard_pending_contract` | Résolution explicite du vrai emprunteur ADEPT ; comparaison du grant v16 et de la liaison Paris historique, refus sur propriétaire et profil divergents | Résolveur neutre par `SourceKey` et grant confirmé, sans inférence de droits |
| `site-7587067dd7068ebdaad80ad9` — façade `LibraryService._paris_adept_identity_for_loan` | `safety_guard_pending_contract` | Façade exécutée avec `connection_identity_id` explicite et codes de refus ; pas de relecture du compte GTK | Appelants migrés vers le même contrat de provenance neutre |
| `site-2e131a36740d4dc492a02de3` — `loan_return_verification._paris_inventory_exact_borrower` | `safety_guard_pending_contract` | Les lignes d'inventaire doivent appartenir à l'identité, credential et emprunteur exacts ; une liste vide d'un autre compte ne prouve jamais le retour | Attestation générique de complétude de branche et de scope source |
| `site-b2c28295eb915336e054f6f1` — façade `LibraryService.verify_paris_return_inventory` | `safety_guard_pending_contract` | Lecture fraîche exactement scoped (GET possible), **aucun second POST** ReturnDigitalLoan ; refus sur scope ou couverture insuffisante | Vérificateur neutre à revocation/CAS et origine source explicites |
| `site-36d7d98c0b5939ee21e6d5cd` — `LibraryService.verify_loan_return` | `legitimate_readonly_compat` | Lecture **locale** de reçus historiques `adobe_first_paris` ; pas de GET/POST, de retour volontaire ou de mutation | Lecture d'archives versionnées préservant les clés et résultats passés |

Deux groupes de décisions distincts sont couverts côté gardes : résolution d'identité ADEPT et vérification d'inventaire prêteur exact ; la lecture de reçus historiques est une compatibilité descriptive indépendante. Les cinq sites restent candidats AST **visibles et qualifiés**, sans être supprimés ou renommés. Aucun crédit de retraite structurelle, aucune nouvelle autorité de droit, aucun relâchement des refus, aucune release.

**Tests source déjà existants :** `test_core_p0_5851_dual_grant_conflict.py`, `test_core_p0_1120_paris_borrower_typed_input.py`, `test_core_p0_2975_return_borrower_scope.py`, `test_core_p0_2975_paris_primary_alias_provenance.py`, `test_core_p0_1120_paris_inventory_completeness.py`, `test_core_p0_2975_typed_return_inventory_absence.py`, `test_p1_4048_legacy_return_seams_closed.py`, `test_core_p0_1120_adept_origin_status_redaction.py`. Nouveau test de cohérence : `test_adept_borrower_and_return_reviewed_sites_preserve_negative_contracts` dans `tests/test_core_p0_5975_business_coupling_ledger.py`.

**Conditions de suite** : migrer le contrat de preuves par source pour les quatre gardes avant retrait physique ; conserver le dernier site pour les archives historiques tant que des reçus restent lisibles. Régression GTK/retour ADEPT et CI/AST/installation/rollback sur HEAD exact pour tout changement ultérieur de comportement.
