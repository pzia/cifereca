# P1 #3687 — filtres Source bornés aux résultats réellement présents

La liste des fournisseurs **connectés / interrogeables** n'est pas une preuve qu'un fournisseur a livré une recommandation. La facette Source d'une vue **locale** doit proposer uniquement les identifiants publics attestés dans `RankedCandidate.sources` parmi les recommandations de la projection courante. Une absence de résultats ne doit pas exposer un menu de catalogues sans éléments. Le contexte réseau reste distinct de la sélection d'un compte, d'une offre ou d'une autorisation.

**Implémentation :** `UxRecommendationsView._reload_providers(providers, ranked_rows)` conserve la validation publique des labels et le plafond des choix existant ; elle utilise les lignes explicites `ranked_rows`, ou à défaut la projection courante `_ranked` si elle existe, pour exclure les fournisseurs sans occurrence dans le classement. Le flux progressif publie d'abord la nouvelle tête `_ranked`, puis ses facettes ; la fin de calcul suit la même règle. Une erreur vide aussi les facettes de langue/source de la projection précédente, comme celles des supports/formats. Les vues historiques appelant `_reload_providers(providers)` sans `ranked_rows` conservent le contrat antérieur.

Les sélections encore valides sont préservées par `LocalQueryControls.set_choices` (#4891) ; les périmées sont retirées. Aucune I/O supplémentaire, aucun changement de classement ni droit présumé.

**Tests :** `test_p1_3687_ranked_source_facets.py` couvre fournisseurs connectés sans résultat, source fusionnée, objets malformés, résultat final vide, erreur de calcul et tête partielle actualisée. La CI du plugin et le GTK E2E Recommandations restent les portes de livraison. Cette tranche ne dispense pas de recette UX complète #774.
