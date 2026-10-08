# Préparer un snapshot plugins-only (prépublication)

Travail préparatoire de #577. Il **ne publie pas de release** et ne modifie pas la version Core.

## Production d'un aperçu vérifiable

Depuis la racine du dépôt :

~~~bash
python -m scripts.package_partial_plugins \
  --packages org.cifereca.recommendations,org.cifereca.provider.gutenberg \
  --id cifereca-plugins-only-preview-manual
python -m scripts.package_partial_plugins --verify dist/cifereca-plugins-only-preview-manual.zip
(cd dist && sha256sum -c cifereca-plugins-only-preview-manual.zip.sha256)
~~~

La sélection porte sur les IDs des packages (champ package.id), jamais sur les dossiers ni sur des chemins arbitraires. Les IDs inconnus, vides ou dupliqués sont refusés. Les manifests et la compatibilité avec la Plugin API des sources courantes sont validés lors du packaging.

Le ZIP reprend le contrat cifereca-dev-snapshot utilisé par cifereca-update-dev : uniquement les packages choisis, aucun wheel Core. Il contient en plus cifereca-plugins-only.json, index de vérification avec versions, IDs des plugins, contraintes Plugin API, SHA-256 de chaque package, SHA source et version Core **de construction** (non une promesse de compatibilité avec toutes les versions Core).

Le fichier .zip.sha256 authentifie le ZIP complet sous réserve que sa provenance soit fiable. Une archive reconditionnée ou son index modifié est rejeté par --verify si les métadonnées ne correspondent plus aux packages.

## CI et installation

Le workflow manuel **Preview plugins-only package** accepte une liste d'IDs séparés par des virgules et dépose le ZIP + son SHA-256 parmi les artefacts GitHub Actions. Il exécute le contrôle des sources, les tests de packaging, la vérification de l'index, des sommes de contrôle et cifereca-update-dev --dry-run --json après installation du Core courant.

Pour une installation manuelle hors du pipeline :

~~~bash
cifereca-update-dev <snapshot.zip> --dry-run --json
cifereca-update-dev <snapshot.zip> --json
~~~

L'updater existant effectue la sauvegarde préalable des plugins touchés, restaure en cas d'échec et lance son smoke test. Il préserve l'activation d'un package déjà installé. Les packages non sélectionnés sont inchangés ; aucun tag ou GitHub Release n'est créé par ce workflow.

**Reste hors de ce groupe** : la signature/provenance forte des publications, la politique de tags plugins-only, la compatibilité Core multi-version, le contrôle d'upgrade/rollback d'une release complète à une partielle, la traçabilité persistante de la provenance dans l'UI et la publication officielle des artefacts. Ces points doivent être validés avant de déclarer #577 terminé et avant toute release publique.
