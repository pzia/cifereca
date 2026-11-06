# #3121 — verdict fermé de capture du premier ACSM Paris

Le claim `loan.borrow` et la confirmation du prêt restent indépendants du
téléchargement transitoire de l'ACSM. Auparavant,
`manual_library_borrow.manual_borrow` ignorait le booléen de
`stage_paris_initial_acsm` et avalait silencieusement toute exception.

Le journal Core publie désormais exclusivement :
`TRACE paris_first_acsm outcome=staged|refused reason_code=<code fermé>`.

Les valeurs autorisées sont `verified_initial_receipt`,
`initial_candidate_unavailable` ou `initial_staging_error`. Aucune
URL, chemin, livre, identifiant de prêt ou détail d'exception. Le
statut `confirmed` du prêt reste une preuve du prêteur, jamais une preuve
de fulfillment ADEPT ou de livraison canonique.

Tests HTTP synthétiques : `tests/test_core_p0_3121_stage_verdict_trace.py`.
