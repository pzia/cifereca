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
