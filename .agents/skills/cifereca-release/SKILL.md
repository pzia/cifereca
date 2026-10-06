---
name: cifereca-release
description: Préparer ou valider une livraison Cifereca : versions, notes, tests, packaging Core/plugins et contrôle des artefacts avant commit ou tag.
---

# Release Cifereca

Utiliser ce skill lorsqu'une tâche demande une nouvelle version, un package installable, un snapshot de développement, des notes de version ou une validation de livraison.

## Déterminer le périmètre

Avant de modifier les versions, identifier si la livraison concerne :

- le Core ;
- un ou plusieurs plugins ;
- le bundle de plugins ;
- un snapshot de développement.

Ne pas incrémenter des versions hors périmètre.

Lire les versions présentes dans `VERSION`, `pyproject.toml` et les manifestes concernés plutôt que supposer une version à partir de la conversation.

## Validation minimale

Pour le Core :

```bash
python -m pytest -q
```

Pour les plugins modifiés :

```bash
(cd plugins/<plugin> && python -m pytest -q)
```

Pour le packaging :

```bash
python scripts/verify-packaging.py
python -m unittest scripts.test_plugin_packaging
```

Ajouter les suites d'intégration, simulation ou GUI lorsque la modification les concerne. Une validation headless n'est pas une validation GUI si GTK n'a pas réellement été exécuté.

## Packaging plugins

Plugin individuel :

```bash
python scripts/package-plugin.py plugins/<plugin>
```

Bundle :

```bash
python scripts/package-all-in-one.py
```

Snapshot développement :

```bash
python scripts/package-dev-snapshot.py
```

Lorsque le binaire installé doit valider le snapshot :

```bash
python scripts/package-dev-snapshot.py --validate-with-update-dev
```

## Artefacts et commit

- Vérifier versions, manifestes et notes de version avant commit/tag.
- Vérifier les SHA-256 et la structure via les scripts du dépôt.
- Ne pas versionner `dist/`, caches, venv ou archives générées sauf demande explicite.
- Dans le compte rendu, donner la version produite, les tests réellement exécutés et le chemin/lien des artefacts demandés.
