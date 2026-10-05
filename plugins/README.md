# Cifereca plugins

Code source des plugins Cifereca — baseline stable **Plugins 0.9.38-dev.6**.

Compatibilité de cette baseline : **Plugin API 1.3**, Core **0.9.41+ / 0.9.42+**. Le contrat Core 0.9.43 / Plugin API 1.4 a été reçu ensuite, mais dev.6 n’avait pas besoin d’être modifié uniquement pour ce contrat.

## Sources publiées

Les sources exactes du snapshot dev.6 sont conservées sous `plugins/source-archives/` au format `.tar.xz`. Cette forme préserve les fichiers source bit-à-bit avec le connecteur de publication disponible.

Exemple :

```bash
tar -xJf plugins/source-archives/baen.tar.xz
```

Pour EPUB Optimizer, extraire les trois parties dans le même répertoire :

```bash
tar -xJf plugins/source-archives/epub_optimizer-part1.tar.xz
tar -xJf plugins/source-archives/epub_optimizer-part2.tar.xz
tar -xJf plugins/source-archives/epub_optimizer-part3.tar.xz
```

Groupes :

- `providers-a.tar.xz` : maintenance-library-audit, BnF Catalogue, Gallica, Project Gutenberg, Kobo ;
- `providers-b.tar.xz` : Numilog, Bibliothèques de Paris, runtime Kobo-Adept.

Archives individuelles : Baen, Diagnostics, Ebooks libres et gratuits, EPUB Optimizer, Accueil.

Exclus volontairement : wheel binaire Pillow embarquée, `.pytest_cache`, ZIP de livraison et autres artefacts de build.

Cette publication correspond à la dernière baseline stable **dev.6** et n’inclut pas les améliorations expérimentales commencées ensuite puis arrêtées.
