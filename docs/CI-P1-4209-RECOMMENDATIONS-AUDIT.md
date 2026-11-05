# Audit CI P1 #4209 — recommendations (05/11/2026)

## Décision

Qualifier `recommendations` comme onzième package produit audité (**11/21**) uniquement après passage de la CI. Les dix autres packages restent non audités et continuent à déclencher le gate plugin exhaustif en cas de changement de code.

## Preuves

- Manifest : `ui.page.recommendations` (UI) et `recommendation.book-reference.recommendations` (service de recommandation), ce dernier déclarant un `requires` sur **la contribution UI du même package**. La fermeture de ce lien est intra-package, sans consommateur externe déclaré.
- Modules `recommendations.py`, `recommendation_bridge.py`, `recommendation_service.py`, `recommendation_llm.py` et `reference_recommendation.py` : API Core publique et modules internes. Pas de dépendance à une implémentation d'un autre plugin produit.
- Le scanner du test `test_recommendations_plugin_ids_and_imports_have_no_other_consumers` surveille des IDs de contributions et imports Python concrets dans tous les autres packages. Les références génériques à des recommandations (mots UI, classements) ne sont pas des dépendances exécutables.

## Garanties de tests

- Changement isolé des modules du package → sélection `src/plugins/recommendations` (1/21).
- Édition du manifest, helper partagé, suppression ou renommage → full 21/21.
- Changement mixte avec Baen non audité ou Baen seul → full 21/21.
- Nouvelle référence concrète à l'ID/service de recommandation dans un autre package → échec du test de garde et réaudit avant toute promotion.

Aucune suppression de test, modification produit, changement d'API, de droits ou de gates Core/E2E/installation. Le gain temporel doit être mesuré sur des runs comparables avant affirmation.
