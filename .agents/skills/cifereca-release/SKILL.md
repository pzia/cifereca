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
(cd src/plugins/<plugin> && python -m pytest -q)
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
python scripts/package-plugin.py src/plugins/<plugin>
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

## Procédure GitHub Actions obligatoire (release explicite)

1. Vérifier que la demande utilisateur autorise **explicitement** une release. « next » ou un merge ne l'autorise pas. Vérifier la version réelle de `main` après intégration des PR.
2. **Avant tout déclenchement**, vérifier la cohérence de `VERSION`, `pyproject.toml`, `src/cifereca/plugin-release.json`, du snapshot des plugins et des manifestes de `src/plugins/`. Exécuter notamment `python -m pytest -q tests/test_core_532_plugin_freshness.py` et les validations packaging ; un plugin modifié entre la préparation et la release impose une nouvelle synchronisation.
3. Vérifier le fonctionnement de `.github/workflows/release.yml` : un push sur `main` déclenche la release uniquement si le **contenu** de `.github/release-trigger` change. Un commit qui réécrit le même contenu ne déclenche rien, même s'il porte un nouveau SHA.
4. Préférer `workflow_dispatch` avec `release_tag=v<VERSION>` lorsqu'un outil autorisé le permet. Sinon, provoquer une **vraie différence de contenu** de `.github/release-trigger` (par exemple une ligne blanche additionnelle), tout en conservant le tag sur la première ligne. Ne pas modifier `VERSION` uniquement pour réessayer.
5. Vérifier qu'un **nouveau run Release** existe pour le SHA attendu, puis consulter son état et ses jobs/logs jusqu'au succès. Un commit créé, un trigger écrit ou un run simplement queued ne prouvent pas que le packaging a réussi.
6. Contrôler que la release GitHub n'est plus draft, que le tag `v<VERSION>` existe et que wheel, bundle/snapshot attendus et `SHA256SUMS` sont publiés. Fournir les **liens directs de téléchargement**. En cas d'échec, diagnostiquer les logs, corriger la cause et relancer avec un déclenchement effectif ; ne jamais annoncer « publiée » avant vérification.

Attention : le workflow actuel peut créer le tag via `gh release create --target ...` lors de la publication ; la responsabilité du Merger couvre **tag + packaging + publication**, pas seulement la PR de préparation.
