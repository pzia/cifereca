# P0 #5975 — Migration SQLite v5 : cohorte historique, pas politique runtime

## Problème

La migration irréversible de v4 vers v5, née dans 0.8.1, corrige les anciennes identités regroupant plusieurs connexions Kobo et plusieurs comptes upstream. Elle peut insérer des identités dérivées, déplacer des références `credential_id` dans `backup_items`, `entitlements` et `acquisition_ledger`, supprimer des projections de compte dupliquées et effacer de vieux bindings ADEPT à portée `account`. Une configuration moderne ou une base de schéma v5+ ne doit jamais réexécuter ces changements.

## Contrat après #6162

La cohorte `_LEGACY_V5_SINGLE_CONNECTION_PROVIDERS = ("kobo",)` est une **constante de données historiques figées**, et non une condition métier du Core, une déclaration plugin ou une demande de nouveau droit. Le code de réparation des anciennes lignes est paramétré par fournisseur et par identité. La partition reste déterministe sur le SHA de `credential_id`, avec suffixe `-2` en cas de collision physique d'identité et conservation des préférences, archives, propriétaires et scopes d'acquisition.

Pour les données Kobo, les résultats doivent être identiques à la migration précédente. Un fournisseur synthétique *injecté uniquement par test* démontre la neutralité de l'algorithme, **sans** introduire de nouvelle limite métier ni de politique persistante pour ce fournisseur. Les index SQL historiques `uq_kobo_connection_identity` / `uq_kobo_account_identity` restent créés en v5 puis remplacés en v15 par les politiques SQL génériques ; ils ne doivent pas devenir une règle de dispatch Core.

Les archives et droits historiques ne sont **pas** de nouvelles attestations A/B. Aucun `SourceReference`, `OriginLink`, profil ADEPT, consentement ou contenu local vérifié n'est fabriqué à partir d'une simple source 0.7.

## Preuves et dette restante

- `tests/test_core_p0_5975_v5_historical_cardinality_scope.py` : vraie base v4 à données Kobo/Paris, compte secondaire, collision d'identité, conservation du SHA/chemin et des droits, liaison ADEPT, redémarrage et cohorte synthétique.
- `tests/test_identities_081.py` et `tests/test_core_p0_5743_sqlite_cardinality_v15.py` : cardinalité et compatibilité historique des migrations v5/v15.
- `docs/provider-business-coupling-review.json` : le writer `legacy-sqlite-v5-kobo-cardinality-identity-repair` reste **actif** dans `retained_legacy_migrations` ; le retrait des sélecteurs codés en dur est enregistré indépendamment, sans double compte de writer ni crédit automatique de retraite AST.

La migration de configuration 0.8.1 pré-tombstone est une **autre** dette historique active. Une suppression de la migration SQLite v5 requiert une décision explicite sur les plus anciennes bases supportées, une preuve de reprise sans perte et un rollback. Pas de release dans #6162.
