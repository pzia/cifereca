# P0 #5083 — Notices de recherche pilotées par les plugins

La recherche CoreUI compose maintenant sa notice de connexion depuis **une projection
générique** des capacités catalogue déjà chargées et des comptes configurés pour
l'identité exactement sélectionnée. Aucune décision n'est fondée sur un identifiant
de marque ; aucun GET, création de session, activation, compte ou acquisition n'est
déclenché au rendu.

Les providers peuvent publier, dans leur `ProviderDescriptor`, une contribution
facultative `search_status_notice_schema="cifereca.provider-search-ui/1"` et
`search_status_notices=((reason_code, text), ...)`. Les codes fermés sont :
`unknown`, `plugin_missing`, `plugin_disabled`, `catalog_unavailable`,
`account_disconnected`, `account_disabled`, `account_unscoped` et
`account_missing`. Le texte est borné à 240 caractères ; les contrôles,
bidi, URLs et signatures de secrets sont refusés. Une contribution malformée
retombe sur une formulation neutre. L'absence d'une ABI n'ajoute jamais de route.

Le plugin Paris 1.2.42 porte désormais les huit formulations françaises
au lieu du `_paris_search_notice` de CoreUI. Les comptes d'autres identités
ne prouvent jamais une session connectée ; les sources publiques non authentifiées
ne génèrent pas de fausse notice de compte. Les capacités authentifiées,
l'inventaire de configuration local et la présence/enabled du plugin restent
distincts. Une rotation d'identité pendant la projection efface son résultat.

Cette contribution est **purement présentative** et ne vaut ni attestation
d'achat, ni preuve de prêt, ni consentement.
