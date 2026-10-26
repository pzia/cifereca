---
name: cifereca-tests
description: Concevoir, placer, exécuter ou réorganiser les tests Cifereca en distinguant unitaires, intégration, fonctionnels, E2E et self-tests de l'outillage.
---

# Tests Cifereca

**Contrat des tests de journalisation :** [cifereca-logging](../cifereca-logging/SKILL.md) et [référence](../../../docs/LOGGING-CONTRACT.md) ; tester les traces brutes, pseudonymisation login/password seulement en IHM, et conserver les fixtures synthétiques.

**Périmètre de confidentialité des tests :** valider seulement les protections nécessaires aux credentials (mots de passe, tokens, cookies authentifiants, clés/secrets API, URLs signées donnant accès) et l'exception d'affichage login/mot de passe dans Diagnostics. N'ajouter ni tests, ni captures expurgées, ni chantier pour masquer des exceptions, codes d'erreur, chemins, comptes, titres, emails ou identifiants ordinaires. Les autres tests d'intégrité/sécurité métier et les gates existants demeurent indépendants.

## Contrat impératif Diagnostics / journal (25/10/2026, #3132)

**Une seule source locale brute, aucun masquage hors de l'IHM Diagnostics.**
Journal, console (miroir), Core, API/broker et export brut conservent **les valeurs telles qu'émises** : opérations, phases, provider, URLs complètes, chemins, comptes, titres, identifiants, codes et corps d'erreur utiles, cookies, tokens et en-têtes compris. Ne jamais substituer `[opération masquée]` à un fait observé par une règle générique d'expurgation. N'inventer ni état, ni HTTP, ni causalité, ni corrélation par horodatage.

**Exception visuelle strictement limitative :** dans le rendu de l'IHM Diagnostics (et les copies du texte affiché), seules **les valeurs explicites de login et de mot de passe** sont pseudonymisées par des alias stables pendant la session (`login-1`, `mot-de-passe-1`). Tout autre champ reste visible. La recherche et l'export brut portent sur les valeurs originales. Ne pas ajouter volontairement de dumps réseau/authentification uniquement pour ce chantier.

Le journal peut contenir des secrets : fichier/rotations locaux restreints (0600), avertissement et confirmation pour l'export brut, **jamais d'envoi automatique**. Les anciennes obligations d'expurgation extensive dans les documents ou tests sont remplacées pour ce périmètre ; conserver les protections spécifiques non liées au journal, sans les présenter comme une politique générale de logs. Contrat et limites : [Journal canonique](../../../docs/DIAGNOSTICS_CANONICAL_JOURNAL.md).



Utiliser ce skill lorsqu'une tâche ajoute des tests, corrige une régression, modifie la stratégie de validation ou déplace des tests.

## Choisir l'emplacement par responsabilité

- `tests/` : Core et scénarios transverses.
- `src/plugins/<plugin>/tests/` : comportement propre au plugin.
- `simulation/tests/` : qualité du banc de simulation lui-même.
- `environnements/<env>/` : self-tests de l'environnement technique.

La dépendance à un faux service ne détermine pas l'emplacement du test. Par exemple, un test de `provider-kobo` contre le faux Kobo reste un test du plugin Kobo.

## Niveaux

- **Unitaire** : fonction/classe isolée, pas de processus ou service réel.
- **Intégration** : plusieurs composants réels du périmètre testé, éventuellement subprocess ou serveur local.
- **Fonctionnel** : comportement utilisateur/Cifereca à travers plusieurs composants.
- **E2E** : chaîne complète avec les frontières principales réellement exercées.
- **Self-test** : vérifie le simulateur ou l'environnement de test, pas Cifereca.

Ne pas appeler « E2E » un test qui remplace l'étape essentielle par un stub.

## Règle simulateur / consommateur

`simulation/` construit le monde fonctionnel de test. Ses tests vérifient que ce monde est fidèle et déterministe.

Les assertions telles que recherche, acquisition, synchronisation, navigation ou rendu Cifereca restent dans les suites Core/plugins/fonctionnelles qui consomment ce monde.

## Exécution

Core :

```bash
python -m pytest -q
```

Plugins :

```bash
for p in src/plugins/*; do
  if [ -f "$p/cifereca-plugin.json" ] && [ -d "$p/tests" ]; then
    (cd "$p" && python -m pytest -q) || exit 1
  fi
done
```

Simulation :

```bash
python -m unittest discover -s simulation/tests -v
```

Après modification d'un faux service, tester à la fois son contrat dans `simulation/tests/` et les consommateurs concernés.

## Régressions

Un test de régression doit décrire l'invariant actuel, pas la version historique qui a introduit le bug. Préférer un nom orienté comportement à un nom du type `test_core041_...` lors d'une création ou d'un renommage explicitement demandé.
