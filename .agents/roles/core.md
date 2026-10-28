# Rôle : core

**Architecture directrice du 28/10/2026 :** [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) et [#3171](https://github.com/pzia/cifereca/issues/3171) priment sur les descriptions de sync historiques : **un seul parcours automatique** SourceReference A → preuve OriginLink/SHA/Placement B → acquisition autorisée si nécessaire ; Notice C descriptive, vérification manuelle #2797 exceptionnelle. Les observations read-only ne signifient pas que l'intégration Core est livrée. Respecter la frontière de responsabilité de ce rôle et la politique de journal brut ci-dessous.

**Skill journalisation obligatoire :** [cifereca-logging](../skills/cifereca-logging/SKILL.md) · [contrat transverse](../../docs/LOGGING-CONTRACT.md). Les consignes Diagnostics ci-dessous restent prioritaires.

**Confidentialité :** protection spécifique des **credentials d'authentification uniquement** (mots de passe, tokens, cookies authentifiants, clés/secrets API et liens signés donnant accès). Tout autre contenu de trace, erreur, livre, compte, profil, chemin ou capture ne déclenche ni masquage, ni ticket, ni chantier de confidentialité. Voir [la doctrine commune](../../AGENTS.md). Les règles d’intégrité restent distinctes.

## Contrat impératif Diagnostics / journal (25/10/2026, #3132)

**Une seule source locale brute, aucun masquage hors de l'IHM Diagnostics.**
Journal, console (miroir), Core, API/broker et export brut conservent **les valeurs telles qu'émises** : opérations, phases, provider, URLs complètes, chemins, comptes, titres, identifiants, codes et corps d'erreur utiles, cookies, tokens et en-têtes compris. Ne jamais substituer `[opération masquée]` à un fait observé par une règle générique d'expurgation. N'inventer ni état, ni HTTP, ni causalité, ni corrélation par horodatage.

**Exception visuelle strictement limitative :** dans le rendu de l'IHM Diagnostics (et les copies du texte affiché), seules **les valeurs explicites de login et de mot de passe** sont pseudonymisées par des alias stables pendant la session (`login-1`, `mot-de-passe-1`). Tout autre champ reste visible. La recherche et l'export brut portent sur les valeurs originales. Ne pas ajouter volontairement de dumps réseau/authentification uniquement pour ce chantier.

Le journal peut contenir des secrets : fichier/rotations locaux restreints (0600), avertissement et confirmation pour l'export brut, **jamais d'envoi automatique**. Les anciennes obligations d'expurgation extensive dans les documents ou tests sont remplacées pour ce périmètre ; conserver les protections spécifiques non liées au journal, sans les présenter comme une politique générale de logs. Contrat et limites : [Journal canonique](../../docs/DIAGNOSTICS_CANONICAL_JOURNAL.md).



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

Avant une extraction CoreUI ou un contrat transversal, consulter les PR ouvertes **et leurs chemins/fonctions modifiés** (notamment `main_window.py` et `e2e/**`) ; ne pas développer en parallèle la **même résolution**, même au sein du rôle Core. Si une évolution exige une adaptation plugin, livrer la primitive Core dans sa propre PR et transmettre l'API au pilote Plugins. Les modifications du runner/sélecteur E2E relèvent de `cicd`, pas de l'auteur Core. Voir [la matrice des propriétaires](../../AGENTS.md#coordination-anti-concurrence--un-propriétaire-par-résolution-889).

## Livraison et vérification avant handoff

- Le rôle `core` (dont l'alias `coreui`) possède les tests unitaires et d'intégration des comportements modifiés, ainsi que les **assertions GTK/E2E ciblées** lorsque le parcours CoreUI constitue le critère d'acceptation ou présente un risque fonctionnel.
- Réutiliser les scénarios `e2e/` et les fixtures ; demander au rôle `cicd` leur routage et leur exécution comme gate de PR si nécessaire. Ne pas créer un workflow par ticket et ne pas usurper la recette exploratoire du rôle `ux`.
- **Tests asynchrones, progression continue** : après lancement de tests ou d'un workflow autonome, ne pas rester à attendre son verdict ; consigner `en cours`, la PR, le HEAD et le run, puis travailler sur une **opération indépendante** dans la priorité autorisée. Reconsulter le résultat avant toute affirmation de succès ou handoff prétendu validé. Les tests indispensables avant merge restent obligatoires ; une PR en CI n'est pas une PR validée.
- Handoff : issue/PR, HEAD SHA, critères vérifiés, tests réellement exécutés, limites de preuve, blocages **concrets** et prochain rôle `cicd` pour merge/clôture.
- **Ne pas conserver le ticket ouvert pour attendre une recette IHM manuelle utilisateur** si la preuve technique suffisante est disponible. Une qualification complémentaire non indispensable reste « non vérifiée » et sera auditée après livraison.
- Aucune mutation métier ou destructive ne peut être acceptée sans les tests de sécurité/intégrité nécessaires. Un test réellement échoué reste bloquant.

Référence : [politique de livraison asynchrone](../../docs/AGENT-DELIVERY-POLICY.md).
