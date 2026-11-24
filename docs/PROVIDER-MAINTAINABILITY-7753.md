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
