# Core P1 #3197 — sources de prêt v1 en synchronisation explicite

Cette tranche corrige la **qualification de complétude** de la commande
`synchronize_account_source(account)`. Une session sans
`loan_inventory_snapshot(account)` peut encore fournir les prêts positifs
via l'API historique `loans(account)`, sans deuxième appel fournisseur.

La réponse v1 (`list[Loan]`) ne démontre ni couverture par branche,
ni fin de pagination, ni échec indépendant d'un sous-service. Le statut de
synchronisation explicite est donc désormais **`partial`**, que
`last_loan_errors` soit absent, vide, rempli ou non fiable. Ce canal
d'erreurs mutable n'est plus consulté par ce consommateur comme preuve de
succès ou d'échec. Cela signifie « inventaire positif uniquement », **pas**
« échec de toutes les requêtes » ; les droits observés continuent d'être
écrits sous contrôles de portée, sans fermeture sur absence.

Les lecteurs typés continuent de porter leur `LoanInventorySnapshot`
atomique. Un `status=success` typé ne vaut toutefois pas à lui seul preuve
de toutes les branches ; l'expiration/réalisation des droits absents reste
conditionnée à `_complete_digital_loan_scope` et à ses contrôles exacts.

**Compatibilité** : l'API plugin v1 `loans()`, les acquisitions manuelles,
la lecture depuis le cache, les droits positifs et les prestataires existants
ne sont pas retirés par cette PR. Seul l'indicateur de complétude du sync
explicite change pour les lecteurs v1. Les consommateurs **opening sync**,
refresh dashboard et retour disposent encore de chemins v1/side-channel :
les migrer séparément sous #3197, avec conformité fournisseur et tests de
non-régression. Aucun changement de version de Plugin API ni release dans
ce lot.
