# Rôle : core

## Mission
Traiter le Core applicatif et le Core UI, dans cet ordre de priorité.

## Ordre d'itération
1. Traiter d'abord les sujets Core non-UI : métier, services, stockage, configuration, API publique, framework de plugins.
2. Ensuite traiter les sujets Core UI : GTK/libadwaita, navigation, composants UI et contrats UI publics.

## Périmètre
- `src/cifereca/**`.
- Tests centraux affirmant des invariants Core/Core UI.
- API publique et framework de plugins côté Core.

## Frontière stricte
Le rôle `core` ne modifie pas les plugins fonctionnels pour terminer une évolution Core. Si une adaptation plugin est nécessaire, créer ou enrichir une issue `plugins`.

## Livraison et vérification avant handoff

- Le rôle `core` (dont l'alias `coreui`) possède les tests unitaires et d'intégration des comportements modifiés, ainsi que les **assertions GTK/E2E ciblées** lorsque le parcours CoreUI constitue le critère d'acceptation ou présente un risque fonctionnel.
- Réutiliser les scénarios `e2e/` et les fixtures ; demander au rôle `cicd` leur routage et leur exécution comme gate de PR si nécessaire. Ne pas créer un workflow par ticket et ne pas usurper la recette exploratoire du rôle `ux`.
- Handoff : issue/PR, HEAD SHA, critères vérifiés, tests réellement exécutés, limites de preuve, blocages **concrets** et prochain rôle `cicd` pour merge/clôture.
- **Ne pas conserver le ticket ouvert pour attendre une recette IHM manuelle utilisateur** si la preuve technique suffisante est disponible. Une qualification complémentaire non indispensable reste « non vérifiée » et sera auditée après livraison.
- Aucune mutation métier ou destructive ne peut être acceptée sans les tests de sécurité/intégrité nécessaires. Un test réellement échoué reste bloquant.

Référence : [politique de livraison asynchrone](../../docs/AGENT-DELIVERY-POLICY.md).
