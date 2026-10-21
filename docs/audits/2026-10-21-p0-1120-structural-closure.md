# #1120 — audit de clôture structurelle ACSM/ADEPT/prêts (21/10/2026)

## Portée et décision

**Périmètre P0 structurel uniquement**, conformément aux arbitrages de
l'issue #1120 des 12, 13 et 18 octobre : frontières d'orchestration,
provenance, sérialisation, idempotence des reçus, absence de mutations
supplémentaires et compatibilité de la façade `LibraryService`.
Cet audit ne requalifie ni ne clôt les parcours fonctionnels reportés dans
#976, #982, #984, #1063, ni le problème d'affichage Paris #2555.

L'audit distingue trois niveaux : inspection de la branche `main`,
tests hors réseau avec SQLite et fournisseurs simulés, et **une seule
validation réelle** en lecture seule de `loans list`. Une CI verte **ne
prouve jamais** un retour Adobe ou Paris effectivement exécuté.

## Matrice d'audit exécutable

| Garantie structurelle | Frontière actuelle | Preuve hors réseau |
| --- | --- | --- |
| Identité de connexion et emprunteur, refus des comptes partagés/non liés | `core/loan_acquisition_admission.py`, `core/loan_adept_profile_guard.py` | `tests/test_adept_borrower_identity_guard.py`, `tests/test_paris_borrower_adept_binding.py`, `test_core_p0_1120_closure_gate.py::test_unbound_paris_borrower_fails_before_provider_and_ledger` |
| Verrou inter-processus **par prêt** + sérialisation locale | `services.py::_acquire_active_loan_automatically` | `test_adept_borrower_identity_guard.py` (concurrence, busy et chemin de verrou), `test_core_p0_1120_closure_gate.py::test_acquisition_facade_keeps_original_signature_and_cross_process_lock` |
| Lecture locale/ledger et refus de réacquérir un ACSM nécessitant une décision | `core/loan_local_preparation.py`, `core/adept_retry_guard.py` | `tests/test_core_0977_adept_retry_guard.py`, `tests/test_core_p0_1120_closure_gate.py` |
| Séquence déterministe admission → contrôle local → `acquisition_put(acquiring)` → fournisseur → profil ADEPT → fulfillment → publication → inspection/indexation → `acquisition_put(complete)` → nettoyage ACSM | `services.py::_acquire_active_loan_automatically_guarded`; modules `loan_provider_delivery`, `loan_adept_fulfillment`, `adept_artifact_publication`, `loan_completion_receipt` | `tests/test_adept_artifact_orchestration.py`, `test_core_p0_1120_closure_gate.py::test_acsm_stages_preserve_admission_provider_adept_index_receipt_order` |
| Façade `LibraryService` inchangée, délégation sous les mêmes verrous | `services.py` : `_acquire_active_loan_automatically`, `return_loan_prefer_adept`, `return_digital`, `_reconcile_adept_acquisition`, `_import_acsm_impl` | `test_core_p0_1120_closure_gate.py::test_return_facades_keep_lock_and_distinct_mutation_seams` et tests de compatibilité Core préexistants |
| Adobe d'abord, claim durable avant mutation, relire les inventaires plutôt que répéter une restitution | `core/return_adept_orchestrator.py`, `core/loan_return_verification.py` | `tests/test_core_0984_adept_first_paris_reconcile.py`, `test_core_p0_1120_closure_gate.py::test_failed_adept_mutation_checkpoint_survives_retry_without_pnb` |
| POST PNB distinct, au plus une fois sans nouveau consentement `explicit_retry is True` et preuve Adobe attestée | `core/provider_loan_return.py` | `tests/test_core_0982_loan_return_reconciliation.py`, `tests/test_core_p0_1120_pnb_retry_strict_boolean.py`, `tests/test_core_p0_1120_return_adobe_attestation_precedence.py`, `test_core_p0_1120_closure_gate.py::test_adept_first_repeated_reads_and_nonboolean_retry_never_repost` |
| Inventaire ADEPT descriptif sans `ok` **uniquement pour** `loans list` en lecture seule, jamais retour/fulfillment | `src/plugins/runtime-kobo-adept/cifereca_kobo_adept_loan_envelope.py`, `cifereca_kobo_adept_runtime.py` | `src/plugins/runtime-kobo-adept/tests/test_adept_p0_1120_readonly_loan_envelope.py`; PR #2610 |

### Recettes synthétiques 0/1/N et redémarrage

Les suites ci-dessus couvrent l'inventaire 0/1/N, des prêts attribués à
plusieurs identités et connexions, un profil manquant ou non prêt, des
enregistrements précédents contradictoires, l'attention Adobe conservée
après redémarrage, la ré-acquisition explicite, les champs mal typés,
les collisions d'artefacts, deux retours concurrents, un POST PNB incertain,
la vérification fraîche sans second POST et la récupération de checkpoint
Adobe **par inventaire uniquement**. Les contrôles sont locaux et simulés.
Aucune donnée de compte, licence ou sortie fournisseur réelle n'est
introduite dans la CI.

### Preuve utilisateur réellement obtenue : **lecture seule**

Le 21/10/2026, après PR #2610 (Kobo-ADEPT runtime 1.2.31),
l'application a publié trois `loans list` `status=success`,
`returncode=0`, `stderr_bytes=0`, dont deux sorties de **6035 octets**
et une de **716 octets**. Avant ce correctif, les mêmes tailles étaient
refusées pour `validation_reason=ok_missing`.
Une vérification locale préalable du format fournisseur comptait
**12 prêts** avec `loans[]`/trois booléens et identifiants distincts.
Les journaux bruts et identifiants restent privés ; voir les commentaires
de #1120 des 21 octobre. Cette preuve confirme le **parsing de la lecture
ADEPT en environnement réel**, et rien au-delà.

## Hors périmètre de la clôture structurelle

- **Aucune recette d'emprunt Paris ni de fulfillment ACSM réel** :
  fournisseur Paris indisponible lors des essais. La CI ne consomme
  jamais de licence Adobe réelle.
- **Aucune restitution Adobe ni POST PNB réels**, aucun rapprochement
  fonctionnel complet prêt Paris ↔ profil Adobe ↔ artefact.
- Les avertissements `[opération masquée]` du diagnostic utilisateur
  sont indéterminés ; ne pas leur attribuer une cause.
- La visibilité UI des prêts Paris reste distincte (#2555).
- Les anomalies métiers #976, #982, #984, #1063 demeurent suivies dans
  leurs tickets indépendants, selon leur priorité propre.

**Critère de clôture #1120 :** audit du graphe de délégation + tests
d'invariants sur `main` après fusion, CI Core/Plugins et
installation/rollback complètes au SHA final de la PR. Si une régression
structurelle apparaît, garder #1120 ouvert et la corriger ; si les
contrôles sont verts, clôturer **uniquement #1120 structurel**, avec
référence au run et cette matrice. Ne pas attendre une mutation réelle
pour prouver un refactoring à comportement constant.
