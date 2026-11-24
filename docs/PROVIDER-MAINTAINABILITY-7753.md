# Paris / BnF maintainability reception (#7753)

Scope: technical refactoring only, no provider mutation, new capability or publication authority.

## Active dependency boundaries

Paris session retains account binding, inventory and download lifecycle. ParisClient composes catalogue (Syracuse search), PNB and transport operations. Selected delivery operations retain the exact scoped DILICOM offer, lease, route witness and staging descriptor; the session retains the actual download implementation. Lazy imports back to the original modules preserve existing patch seams and avoid import-time cycles; they are dependencies, not independent clients.

BnF session delegates history reads and borrower qualification to provider-local modules. History is a private explicit qualification read, not an enabled history capability. DOM probes, cover matrix and qualification evidence must not be interpreted as execution authorization. Existing web/HTTP, catalogue, borrow and signed-action boundaries remain provider-local.

Core LegacyRightsResourceAdapter projects legacy snapshots and stages candidates with caller-supplied admission checks. No concrete provider imports across packages. Paris DILICOM/SYRACUSE coverage and EbookUrl differ from BnF bnf_html and reader-acs4; authentication, redirects, completeness and action admission remain separate. A candidate is not publication authority.

## Measured intermediate comparison

All after values at f0db0bd61eaf0c9f9915a2471f70c7ba9927cc56. Lines count newline characters; bytes count UTF-8. Before references are intermediate commits, not the initial audit baseline.

| File | Before commit | Before lines / bytes | After lines / bytes |
|---|---|---:|---:|
| cifereca_paris_client.py | 3edbb9dc68ba | 1136 / 57279 | 378 / 17912 |
| cifereca_paris.py | c1cd6f35d8fa | 2270 / 117352 | 2042 / 104383 |
| cifereca_bnf_pret.py | c1cd6f35d8fa | 1585 / 86548 | 1373 / 74989 |

New boundaries at after SHA: Paris catalogue 351 lines, PNB 552, transport 264, selected delivery 195; BnF history 66, borrower qualification 176. Paris client directly declares 16 methods versus 38 at its intermediate reference; inherited entry points remain. These figures show redistribution, not total-code reduction or a cyclomatic complexity measurement.

## Reception evidence

PRs #7943, #7944, #7945, #7946, #7947, #7948 and #7950 merged after exact-head checks. #7946 head c0ef159decda: PR CI 45350890128, E2E 45350890179, docs 45350890112, full install 45350890247, ADEPT 45350890287, AST 45350890251 all success. #7950 head a751fea2200b: PR CI 45353349119 and ADEPT 45353349068 success after restoring trace logger binding.

Remaining before closure: initial-baseline complete module inventory, explicit parity matrix mapped to test cases (including 0/1/N, ABA, revocation, staging and at-most-once), final integrated gates and dependency verification of qualification-only modules. Do not infer live provider qualification or close #7753 from line counts.

## R4 — verified test inventory

### `tests/test_core_p1_3122_real_manifest_lending_conformance.py`

- `test_real_packages_publish_the_same_provider_id_version_and_lending_api`
- `test_real_paris_manifest_matches_lending_methods_but_legacy_return_renew_unmapped`
- `test_real_bnf_internal_acsm_does_not_become_public_download_or_mutation`
- `test_real_manifest_retains_search_read_separation_without_unverified_actions`

### `src/plugins/provider-paris/tests/test_p0_7231_acsm_offer_route.py`

- `test_offer_bound_route_does_not_infer_from_media_format`
- `test_signed_query_rotation_preserves_route_id`
- `test_route_change_in_final_inventory_refuses_before_get`
- `test_complete_source_cycle_reuses_route_class_without_new_inventory`
- `test_real_selected_paris_route_reports_only_qualified_get_retry`

### `src/plugins/provider-paris/tests/test_p0_3723_pnb_selected_staging.py`

- `test_paris_selected_pnb_get_stage_swap_never_writes_outside`
- `test_paris_selected_candidate_does_not_overwrite_existing_file`
- `test_paris_atomic_candidate_link_swap_removes_candidate_from_old_inode`

### `src/plugins/provider-bnf-pret/tests/test_p0_3723_staging_inode.py`

- `test_bnf_acsm_staging_swap_never_writes_or_keeps_file_outside`
- `test_bnf_valid_acsm_still_staged_exactly_once`

Shared actual-class coverage in `tests/test_core_p1_3122_paris_bnf_snapshots.py`: two-branch uncertainty, partial positive coverage, failed branches, linked reader separation, BnF scoped empty inventory, wrong credential/closed session, invalid DOM, mid-read reader replacement, adapter opt-in and wrapper revocation. Shared adapter tests in `tests/test_p1_7753_legacy_rights_adapter.py`: single delegation, explicit action, scope/admission cleanup, outside staging and symlink preservation, non-publication candidate and ABA admission revocation. These are verified test definitions, not an assertion that every R4 scenario is covered on both providers.


## R5 — exact pre-extraction baseline

Baseline is the base of #7943, `5552518195074d598efb9d609f96b53f3a2bff96`; comparison is `0e86b39364863f804cafb90895c5049bf08876f0`. Counts are newline characters, UTF-8 bytes and directly indented def declarations, not cyclomatic complexity or total inherited API size.

| Entry module | Lines before / after | Bytes before / after | Direct defs before / after |
|---|---:|---:|---:|
| Paris session | 2270 / 2042 | 117352 / 104383 | 47 / 42 |
| Paris client | 1467 / 378 | 76343 / 17912 | 45 / 16 |
| BnF session | 1639 / 1373 | 89486 / 74989 | 36 / 33 |

The three entry modules total 5376 -> 3793 lines and 283181 -> 197284 bytes. Extracted modules remain part of the implementation; these figures measure entry-module concentration, not total code deletion. Full package inventory and integrated gates remain separate reception requirements.


## R4 — explicit scenario mapping (verified definitions at de3b54787fa9a9bfe2750a77ca386ac83d378ed0)

Paris references below are relative to src/plugins/provider-paris/tests;
BnF references are relative to src/plugins/provider-bnf-pret/tests.

| Scenario | Paris | BnF |
|---|---|---|
| Revoked admission before private read | test_p0_3723_downloadable_v2.py::test_guarded_v2_candidate_requires_live_core_lease_before_private_read | test_p0_3723_guarded_core_admission.py::test_guarded_candidate_rejects_revoked_lease_before_private_get |
| Revocation during delivery | test_p0_3723_downloadable_v2.py::test_guarded_v2_candidate_forwards_live_lease_and_discards_revoked_staging | test_p0_3723_guarded_core_admission.py::test_revocation_during_fresh_activity_prevents_token_get |
| Rebind | test_p0_3723_downloadable_v2.py::test_v2_rebind_during_candidate_does_not_return_or_keep_staging | test_p0_3633_selected_acsm_staging.py::test_rebound_session_instance_or_credential_never_stages_or_gets_stale_token |
| Changed/retracted offer | test_p0_7231_acsm_offer_route.py::test_route_change_in_final_inventory_refuses_before_get | test_p0_3633_selected_acsm_staging.py::test_second_fresh_activity_does_not_get_when_offer_retracted |
| Unsafe redirect | test_p0_3121_selected_no_mutating_redirect.py::test_selected_redirect_cannot_issue_second_get_to_mutating_path | test_p0_3867_signed_redirects.py::test_untrusted_delivery_redirect_never_follows_url |
| Staging isolation | test_p0_3723_pnb_selected_staging.py::test_paris_selected_pnb_get_stage_swap_never_writes_outside | test_p0_3723_staging_inode.py::test_bnf_acsm_staging_swap_never_writes_or_keeps_file_outside |

The shared adapter ABA test covers admission revocation even after scope restoration;
it is not proof of every provider-specific ABA interleaving. The real-session
empty BnF projection regression was integrated in #7953 (HEAD 82c28aeb5919,
PR CI 45355408424 success). Paris inventory completeness remains unknown,
not a BnF-style attestation. Missing explicit symmetric 0/1/N, invalid ACSM,
consent and at-most-once mappings must not be marked complete from this table.


## R4 — complementary reception at cf460365ad0a47ab09e508cd7a277559a17a6756

| Scenario | Paris | BnF |
|---|---|---|
| 0/1/N inventory | Core test_paris_real_inventory_cardinality_preserves_branch_coverage (#7966) | Core test_bnf_validated_html_claims_scoped_coverage_including_empty and test_bnf_zero_remaining_quota_preserves_active_inventory (#7958) |
| Explicit selected delivery consent | test_p1_3252_paris_selected_right_freshness.py::test_paris_invalid_selection_never_gets_content | test_p0_3633_selected_acsm_staging.py::test_foreign_right_edition_offer_or_policy_refused_before_any_get |
| Invalid ACSM | test_p0_3723_pnb_selected_staging.py::test_paris_invalid_selected_acsm_never_stages_or_retries (#7967) | test_p0_3633_selected_acsm_staging.py::test_http_failures_stay_in_core_staging_boundary |
| Ambiguous borrow, spent claim | test_p0_3723_claimed_borrow.py::test_unconfirmed_attempt_is_not_success_or_retry | test_p0_2055_borrow_explicit_execution.py::test_timeout_still_spends_claim_without_automatic_second_post |
| Existing loan | test_p0_3723_claimed_borrow.py::test_existing_loan_never_repeats_borrow_or_download | test_p0_2055_borrow_explicit_execution.py::test_positive_existing_loan_refuses_before_post |

Paris uses a potentially mutating GET for its explicit borrow route; BnF uses its observed action sequence, not a common POST protocol. Neither may replay an ambiguous mutation. Correction to the earlier matrix: Paris successful empty ListLoans branches are complete; nonempty synthetic rows without raw provenance are unknown. BnF synthetic empty manual inventory is complete but rights/v2 remain partial (#7953). These are different authority boundaries.

Exact-head reception: #7966 bcfb747768ae, PR CI 45367735750 success; #7967 42bac5d73a10, PR CI 45370618617 and ADEPT guard 45370619198 success. Remaining: complete package measurement, cancellation mapping and final integrated gates.


## R5 — package-level measurement

Baseline 5552518195074d598efb9d609f96b53f3a2bff96; comparison 75d8f5b42f92546e13a45a40840ac5250039abae. Enumerated top-level Python implementation modules via repository code search, fetched every listed path at both exact SHAs (six new paths return 404 at baseline). Counts match the baseline audit's 9 Paris and 17 BnF modules. Search enumeration is not an independent recursive tree audit. Tests and packaging metadata excluded.

| Package | Modules before / after | Lines before / after | UTF-8 bytes before / after |
|---|---:|---:|---:|
| Paris | 9 / 13 | 4996 / 5041 | 246661 / 247923 |
| BnF | 17 / 19 | 5151 / 5135 | 244361 / 242881 |
| Total | 26 / 32 | 10147 / 10176 | 491022 / 490804 |

Paris session + client concentration: 193695 / 246661 bytes (78.5%) to 122295 / 247923 (49.3%). BnF session + web: 128576 / 244361 (52.6%) to 114079 / 242881 (47.0%). Total package size essentially stable; maintainability improvement is redistribution and neutral adapter reuse, not wholesale code removal.

Paris measured suffixes (prefix cifereca_paris): session, client, trace, borrow, formats, normalize, constants, bibliography, selected_staging; added pnb, transport, catalogue, selected_delivery. BnF (prefix cifereca_bnf_pret): session, web, http, opds2, paging, borrow, catalog, contract, download, dom_probe, selection, book_actions, observations, cover_matrix, qualification, signed_action, loan_selection; added history and borrow_qualification.

R4 cancellation: Paris test_p1_3122_cancelled_loan_inventory.py::test_cancel_propagates_and_never_generates_inventory_status; BnF tests/test_core_p1_3122_paris_bnf_snapshots.py::test_bnf_cancelled_activity_never_returns_inventory. #7969 exact HEAD 2d150df3e816, PR CI 45371277218 success. Both legacy and typed inventory paths covered; cancellation is not partial success.

Remaining: final integrated gates and qualification-only dependency verification. No live supplier qualification inferred.
