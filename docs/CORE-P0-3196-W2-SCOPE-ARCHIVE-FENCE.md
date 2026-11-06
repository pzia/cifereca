# Core P0 #3196 / W2 — une archive ne réarme pas un refus de délégation

Le statut historique Kobo `attention_required / missing_delegation_scope`
est un checkpoint d'échec de délégation (403) nécessitant une reprise explicite
propre au compte. Le manifeste Kobo peut présenter la même édition comme
`archived=True`, puis ultérieurement `archived=False`.

**Invariant :** le branchement `_skip_manifest_item` peut ignorer une édition
archivée sans lancer de GET, mais ne doit pas remplacer ce refus durable par
`skipped_archived`. Sans cette garde, le dé-archivage supprimerait la protection
non-retryable et autoriserait une nouvelle acquisition automatique sans
réautorisation. Les éditions réellement archivées sans refus préalable continuent
d'évoluer vers `skipped_archived` conformément au contrat existant.

Régression isolée : `tests/test_core_p0_3196_w2_scope_archive_retention.py` ;
fixture locale, aucune requête Kobo/ADEPT. Cette tranche ne remplace pas les
writers 0.7 et ne démontre pas le cutover global #3196.
