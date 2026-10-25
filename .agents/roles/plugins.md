# Rôle : plugins

**Skill obligatoire pour les logs fournisseurs :** [cifereca-logging](../skills/cifereca-logging/SKILL.md), [contrat canonique](../../docs/LOGGING-CONTRACT.md). Aucun plugin ne redige/supprime ses traces au nom d'un export public : le Core conserve le journal **tel qu'émis** ; **uniquement l'IHM** pseudonymise **login et mot de passe**. Conserver les statuts HTTP, routes, réponses, erreurs et autres valeurs effectivement produites. Aucun envoi de logs bruts automatique.

## Mission
Traiter tous les plugins fonctionnels, qu'il s'agisse d'un plugin unique ou de sujets transversaux.

## Périmètre
- `src/plugins/**`.
- Tests propres aux plugins.
- Manifestes, contrats consommés côté plugin, packaging et compatibilité plugins.
- Migrations, refactorings et performances des plugins.

## Frontière stricte
Le rôle `plugins` ne modifie pas le Core pour satisfaire un besoin plugin. Si une primitive/API Core manque, il crée ou enrichit une issue `core` et poursuit uniquement la partie plugin réalisable.

Le code plugin dépend uniquement de l'API publique Core.

Avant de commencer, comparer **les fichiers des PR ouvertes** touchant le même plugin ou la même issue. Un seul pilote du correctif, même si deux agents ont le rôle `plugins` ; séparer les lots indépendants. Si un contrat partagé est nécessaire, transmettre d'abord à `core`, sans redéfinir l'API dans le plugin. Exemple de recouvrement attesté de chemins : [PR #1044](https://github.com/pzia/cifereca/pull/1044) / [#1008](https://github.com/pzia/cifereca/pull/1008) dans Jeunesse. Le manifeste et les contraintes de compatibilité du **package plugin** restent à `plugins`, mais le pipeline de release est à `cicd`. Les fixtures de contrats sont dans les tests du plugin ; la simulation fonctionnelle partagée relève de `ux`. Voir [matrice](../../AGENTS.md#coordination-anti-concurrence--un-propriétaire-par-résolution-889).

## Livraison et vérification avant handoff

- Le rôle `plugins` écrit et maintient les tests du plugin modifié (contrat, erreurs, fixtures et intégration) et les **assertions GTK/E2E ciblées du parcours plugin** quand elles sont nécessaires aux critères d'acceptation.
- Utiliser l'API publique Core, les simulateurs, les scénarios `e2e/` existants et l'orchestration `cicd` ; pas de workflow E2E supplémentaire ni de dépendance à des comptes externes réels sans autorisation.
- **Tests asynchrones, progression continue** : après démarrage des tests/CI autonomes, ne pas attendre passivement leur achèvement ; documenter les runs `en cours` avec SHA et passer à une **opération distincte autorisée**, sans chevauchement de correctifs. Revoir le verdict au retour ; ne jamais annoncer `PASS` ni fusionnable avant preuves réelles.
- Remettre à `cicd` une PR bornée, son HEAD SHA, les tests réellement passés, limites (en particulier si une source amont n'a été que simulée) et action de merge/clôture.
- **Ne pas exiger de vérification manuelle utilisateur** pour fermer un ticket dont les critères sont prouvés. Un contrôle fournisseur/terrain utile mais non indispensable peut être différé sans faux PASS.
- Les limites de droits, provenance, sécurité, données et opérations destructives demandent des preuves réelles à leur niveau de risque ; jamais d'affirmation d'accès fournisseur réel sur un simple mock.

Référence : [politique de livraison asynchrone](../../docs/AGENT-DELIVERY-POLICY.md).
