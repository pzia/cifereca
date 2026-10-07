---
name: cifereca-tests
description: Concevoir, placer, exécuter ou réorganiser les tests Cifereca en distinguant unitaires, intégration, fonctionnels, E2E et self-tests de l'outillage.
---

# Tests Cifereca

Utiliser ce skill lorsqu'une tâche ajoute des tests, corrige une régression, modifie la stratégie de validation ou déplace des tests.

## Choisir l'emplacement par responsabilité

- `tests/` : Core et scénarios transverses.
- `plugins/<plugin>/tests/` : comportement propre au plugin.
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
for p in plugins/*; do
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
