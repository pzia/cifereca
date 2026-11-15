# P0 #5975 — Réparation de cardinalité historique 0.8.1 (PR #6141)

## Périmètre exécutable

`Config.load()` invoque encore `_migrate_legacy_account_cardinality_once` pour les anciennes configurations ayant des comptes fournisseurs sans `migration_history.legacy_account_cardinality_v1`. Le traitement peut créer des identités dérivées **en mémoire** et modifier l'`identity_id` des comptes historiques. Seul `Config.save()` valide ces changements sur disque ; le chargement seul ne sauvegarde rien. Il ne réalise ni GET/POST fournisseur, ni attribution de droits, ni consentement de synchronisation, ni attribution de profil ADEPT.

## Découplage limité, sans faux crédit

L'ancienne comparaison explicite de fournisseur `!= "kobo"` est retirée. La cohorte historique figée `_LEGACY_081_SINGLE_CONNECTION_SOURCES` contient `kobo` **en tant que donnée de migration**, sans invoquer les descripteurs plugin ni leur appliquer rétroactivement une nouvelle politique. Le réparateur travaille désormais sur `(provider_id,identity_id)`, avec suffixe déterministe fondé sur `credential_id`, reprise des préférences non sensibles et nom de dossier désambiguïsé. Un fournisseur synthétique vérifie que l'algorithme ne dépend pas de la marque ni du compte d'un autre fournisseur.

**Ce n'est pas l'extinction du writer.** Le registre archive le signal AST physique sans le créditer comme disparition de dette (`in_ast_metric=false`) et maintient explicitement `retained_legacy_migrations` avec `classification=legacy_mutating_cutover`. L'inventaire métier conserve ce chemin dans `active_legacy_writers_to_retire`, y compris lorsque le compteur lexical AST baisse.

## Conditions de retrait ultérieur

Le writer ne pourra être supprimé qu'après preuve de traitement des anciennes configurations prises en charge, d'une stratégie d'import/rollback sans perte des comptes et identités et de la conservation du veto sur les marqueurs inconnus ou corrompus. Les marqueurs déjà persistés ne sont ni relus comme autorité de droit, ni supprimés. La migration SQLite v5 des anciennes bases et sa migration v15 de contraintes cardinalité restent **indépendantes et intactes**.

Tests : `tests/test_core_p0_5975_legacy_cardinality_single_run.py` et `tests/test_core_p0_5975_business_coupling_ledger.py`. Gates attendus : PR CI, AST et installation/rollback sur HEAD exact. Aucune release.
