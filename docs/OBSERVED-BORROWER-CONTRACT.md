# Compte emprunteur observé : prédicat transversal

`cifereca.plugin_api.loan_scope.loan_matches_account(loan, account, descriptor)`
extrait le contrôle déjà utilisé par `LoanActionScopeLease.current`. Il réutilise
`ProviderDescriptor.existing_primary_loan_account_alias` ; aucun nouveau type,
service, registre ou champ de configuration n'est créé. API additive 1.4.8.

Le résultat porte seulement sur `(identity, provider, credential, borrower)`.
Le fournisseur est une coordonnée opaque. Un alias déclaré par le descripteur
installé est admis uniquement si `Account.unique_id == ''` et `is_primary is True`.
Sans alias, seule l'égalité littérale du compte convient. Un descripteur absent,
mal formé ou appartenant à un autre fournisseur refuse même un alias ressemblant.
Aucun alias ne vient de `Loan.raw`, du titre ou d'une notice de catalogue.

Cette comparaison ne vérifie ni état actif, édition, occurrence, consentement,
propriété SQLite, session enregistrée, epoch ni résultat distant. Elle ne donne
aucune permission d'emprunter, de renouveler, de télécharger ou de fermer un prêt.
Le bail existant conserve **tous** ces contrôles indépendants et consomme désormais
le prédicat partagé. Aucun appel réseau ni écriture n'est ajouté.

## Points de migration sans modification des appelants

| Groupe AST actuel | Adoption possible / garde à conserver |
| --- | --- |
| `plugin_api/lending_actions._exact_target` | Réutiliser le descripteur installé du compte pour la seule comparaison de provenance ; conserver admission/consentement/état. |
| `plugin_api/loan_inventory._loan_matches_scope`, `sync_coordination._opening_loans_scoped` | Adapter à la frontière disposant du vrai compte et du descripteur ; ne pas fabriquer un Account depuis un snapshot ni élargir la couverture complète. |
| `services._cached_loans_for_exact_account`, vérification retour/renouvellement | Comparer le prêt au compte vivant avant d'utiliser une observation ; conserver occurrence, branche, epoch et vérification distante. |
| `library_declared_links._account_current` | Seule la convention d'alias est concernée ; conserver unicité du compte, scope exact et transaction. Ne pas transformer une déclaration subjective en prêt observé. |

L'insuffisance n'était pas l'absence d'une politique : elle existe en production.
Son implémentation était enfermée dans un bail d'action complet, inutilisable
pour une simple comparaison passive sans reconstruire service/session/stockage.
L'extraction ne change pas les consommateurs réservés aux agents fournisseurs.
Les groupes ci-dessus sont des points de migration, pas des suppressions prouvées
ni un décompte de lignes AST éliminées. Les anciens tests demeurent jusqu'au cutover.
