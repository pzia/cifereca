---
name: cifereca-plugin
description: Créer, modifier ou valider un plugin Cifereca en respectant l'API publique, les contrats, les tests locaux et le packaging du dépôt.
---

# Plugin Cifereca

## Contrat impératif Diagnostics / journal (25/10/2026, #3132)

**Une seule source locale brute, aucun masquage hors de l'IHM Diagnostics.**
Journal, console (miroir), Core, API/broker et export brut conservent **les valeurs telles qu'émises** : opérations, phases, provider, URLs complètes, chemins, comptes, titres, identifiants, codes et corps d'erreur utiles, cookies, tokens et en-têtes compris. Ne jamais substituer `[opération masquée]` à un fait observé par une règle générique d'expurgation. N'inventer ni état, ni HTTP, ni causalité, ni corrélation par horodatage.

**Exception visuelle strictement limitative :** dans le rendu de l'IHM Diagnostics (et les copies du texte affiché), seules **les valeurs explicites de login et de mot de passe** sont pseudonymisées par des alias stables pendant la session (`login-1`, `mot-de-passe-1`). Tout autre champ reste visible. La recherche et l'export brut portent sur les valeurs originales. Ne pas ajouter volontairement de dumps réseau/authentification uniquement pour ce chantier.

Le journal peut contenir des secrets : fichier/rotations locaux restreints (0600), avertissement et confirmation pour l'export brut, **jamais d'envoi automatique**. Les anciennes obligations d'expurgation extensive dans les documents ou tests sont remplacées pour ce périmètre ; conserver les protections spécifiques non liées au journal, sans les présenter comme une politique générale de logs. Contrat et limites : [Journal canonique](../../docs/DIAGNOSTICS_CANONICAL_JOURNAL.md).



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
