# Rôle : plugins

**Architecture directrice du 28/10/2026 :** [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) et [#3171](https://github.com/pzia/cifereca/issues/3171) priment sur les descriptions de sync historiques : **un seul parcours automatique** SourceReference A → preuve OriginLink/SHA/Placement B → acquisition autorisée si nécessaire ; Notice C descriptive, vérification manuelle #2797 exceptionnelle. Les observations read-only ne signifient pas que l'intégration Core est livrée. Respecter la frontière de responsabilité de ce rôle et la politique de journal brut ci-dessous.

**Skill journalisation obligatoire :** [cifereca-logging](../skills/cifereca-logging/SKILL.md) · [contrat transverse](../../docs/LOGGING-CONTRACT.md). Les consignes Diagnostics ci-dessous restent prioritaires.

**Confidentialité :** seuls les credentials (mot de passe, token d'accès/refresh, cookie de session authentifiant, secret API/clé privée, URL signée d'accès) justifient une protection spécifique. Erreurs, messages HTTP, chemins, titres, comptes, identifiants et captures sans secret n'imposent aucun ticket, expurgation ou correctif de confidentialité. Voir [la doctrine commune](../../AGENTS.md). Ne pas élargir un ticket Plugins pour cela.

## Contrat impératif Diagnostics / journal (25/10/2026, #3132)

**Une seule source locale brute, aucun masquage hors de l'IHM Diagnostics.**
Journal, console (miroir), Core, API/broker et export brut conservent **les valeurs telles qu'émises** : opérations, phases, provider, URLs complètes, chemins, comptes, titres, identifiants, codes et corps d'erreur utiles, cookies, tokens et en-têtes compris. Ne jamais substituer `[opération masquée]` à un fait observé par une règle générique d'expurgation. N'inventer ni état, ni HTTP, ni causalité, ni corrélation par horodatage.

**Exception visuelle strictement limitative :** dans le rendu de l'IHM Diagnostics (et les copies du texte affiché), seules **les valeurs explicites de login et de mot de passe** sont pseudonymisées par des alias stables pendant la session (`login-1`, `mot-de-passe-1`). Tout autre champ reste visible. La recherche et l'export brut portent sur les valeurs originales. Ne pas ajouter volontairement de dumps réseau/authentification uniquement pour ce chantier.

Le journal peut contenir des secrets : fichier/rotations locaux restreints (0600), avertissement et confirmation pour l'export brut, **jamais d'envoi automatique**. Les anciennes obligations d'expurgation extensive dans les documents ou tests sont remplacées pour ce périmètre ; conserver les protections spécifiques non liées au journal, sans les présenter comme une politique générale de logs. Contrat et limites : [Journal canonique](../../docs/DIAGNOSTICS_CANONICAL_JOURNAL.md).



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
