---
name: cifereca-plugin
description: Créer, modifier ou valider un plugin Cifereca en respectant l'API publique, les contrats, les tests locaux et le packaging du dépôt.
---

# Plugin Cifereca

**À appliquer pour tout provider/runtime** : [skill de journalisation](../cifereca-logging/SKILL.md) et [contrat Core/Plugins](../../../docs/LOGGING-CONTRACT.md). Journaliser intégralement les événements et détails effectivement produits, **sans masquer dans le plugin** ; seuls **login/mot de passe** sont pseudonymisés **dans le rendu de l'IHM Core**, les autres champs restant visibles. Les logs bruts ne doivent pas être publiés automatiquement.

Utiliser ce skill pour toute modification sous `src/plugins/<plugin>/` ou pour créer un nouveau plugin.

## Principes

1. Lire le `cifereca-plugin.json` du plugin et l'API publique courante sous `src/cifereca/plugin_api/` avant d'introduire une dépendance au Core.
2. Le code de production du plugin peut dépendre de `cifereca.plugin_api`, mais pas des modules privés `cifereca.*`.
3. Déclarer explicitement dans le manifeste les contrats, brokers, capabilities et prérequis nécessaires. Ne pas contourner la négociation de contrats par un import privé.
4. Conserver les tests spécifiques dans `src/plugins/<plugin>/tests/`.
5. Si le comportement dépend d'un service externe simulé, utiliser le banc `simulation/` plutôt que réimplémenter un faux service local, sauf faux minimal strictement unitaire.

## Tests

Lancer les tests depuis le répertoire du plugin afin d'éviter les collisions entre packages `tests` :

```bash
(cd src/plugins/<plugin> && python -m pytest -q)
```

Pour une modification de contrat public, exécuter aussi les tests Core concernés sous `tests/`.

Pour un plugin qui consomme un faux service partagé, exécuter également le self-test correspondant du banc de simulation et les intégrations consommatrices pertinentes.

## Packaging

Avant de considérer un plugin livrable :

```bash
python scripts/verify-packaging.py
python -m unittest scripts.test_plugin_packaging
python scripts/package-plugin.py src/plugins/<plugin>
```

Le manifeste doit être à la racine du ZIP. Ne pas versionner le ZIP produit sauf demande explicite.

## Portée

Si l'assertion principale d'un scénario porte sur plusieurs plugins, le Core ou l'IHM complète, ne pas forcer ce scénario dans les tests du plugin : le placer dans la suite transverse appropriée.
