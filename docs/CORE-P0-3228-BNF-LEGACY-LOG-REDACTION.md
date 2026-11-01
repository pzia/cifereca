# P0 #3228 — neutraliser les fuites du journal BnF auto historique

Le chemin `bnf_auto_materialization` est une compatibilité temporaire,
distincte de l'exécuteur canonique A→B. Au 01/11/2026, huit événements
de ce worker pouvaient afficher directement `identity_id`, `credential_id`,
`account_id`, `loan_id`, et joindre `exc_info=True`. Une exception
I/O pouvait contenir un chemin privé, une URL signée, ou un jeton.

**Contrat corrigé** : ces huit événements ne publient plus que les
énumérations fermées `TRACE bnf_auto code=...` et le cas échéant
`stage=...`. Pas de variable interpolée, nom de livre, compte, prêt,
chemin, URL, trace Python ou message d'exception. Un échec de contrôle
retourne les mêmes `status/code` existants ; aucun changement
d'autorisation, d'ACSM, d'ADEPT, de retry ou de chemin utilisateur.

`tests/test_core_p0_3228_bnf_legacy_log_redaction.py` valide
statiquement tous les appels du logger concerné et provoque un
`OSError` contenant un canari privé pendant le contrôle de reçu.
Le journal ne doit contenir que le code de phase fermé.

Cela **ne remplace pas** le chantier #3228 : le worker parallèle BnF
reste à supprimer au profit du moteur Core unique une fois les preuves
de livraison ADEPT et de migration du consentement établies. Une
recette terrain consentie et une preuve physique `Placement/OriginLink`
restent nécessaires. Aucun identifiant personnel ni ZIP brut dans
GitHub.
