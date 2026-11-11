# #3121 — verdict fermé du premier ACSM (contrat Core / provider)

La création de prêt, le claim durable `loan.borrow` et la livraison d'un
jeton ACSM sont des opérations **distinctes**. La synchronisation ne crée
jamais de nouvel emprunt et un résultat ambigu interdit un second POST ou
un second GET mutateur de confirmation.

Après une confirmation exacte et durable du prêt, le Core appelle
**facultativement** `session.initial_loan_acsm_candidate(...,
admission_current=...)`. Seul le provider sait reconnaître le premier lien
ACSM signé du portail et délègue son transfert au service HTTP(S) Core.
L'absence de cette méthode n'active aucune livraison par défaut.

Le Core `manual_library_borrow` garantit avant et après ce GET :

- même identité, compte, credential, emprunteur et occurrence de prêt ;
- claim confirmé au même `operation_id` et `verified_loan_id` ;
- contexte vivant, sans reprise implicite de l'emprunt ;
- chemin de staging physique sans symlink et contrôle du format ACSM ;
- reçu `acsm_cache.remember_initial` lié au claim, sans URL persistée.

Le journal Core n'émet que
`TRACE initial_loan_acsm outcome=staged|refused reason_code=<code fermé>`.
Codes : `verified_initial_receipt`, `initial_candidate_unavailable`,
`initial_staging_error`. Aucun chemin, jeton, URL, compte ou détail
d'exception n'est journalisé.

L'échec de staging ne révoque jamais le prêt confirmé et n'autorise aucun
nouvel emprunt. Le staging n'est ni un artefact final ni une preuve ADEPT.

Test HTTP et SQLite synthétique :
`tests/test_core_p0_3121_stage_verdict_trace.py`.
