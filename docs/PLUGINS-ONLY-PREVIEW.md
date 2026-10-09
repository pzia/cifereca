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

Le ZIP reprend le contrat cifereca-dev-snapshot utilisé par cifereca-update-dev : uniquement les packages choisis, aucun wheel Core. Le manifest principal porte `scope: "plugins-only"` et l'index obligatoire `cifereca-plugins-only.json` recense versions, IDs des plugins, contraintes Plugin API, SHA-256 de chaque package, SHA source et version Core **de construction** (non une promesse de compatibilité avec toutes les versions Core). Si l'index manque malgré ce marqueur, l'updater refuse la mise à jour **avant toute sauvegarde ou installation**, même en mode `--dry-run`. Les snapshots dev génériques historiques sans marqueur restent compatibles.

Le fichier `.zip.sha256` permet de vérifier la cohérence du ZIP avec sa somme publiée, **mais pas l'identité du producteur** si les deux fichiers proviennent d'une même source non fiable. Un ZIP et son index modifiés de façon cohérente peuvent passer le contrôle interne `--verify`.

Pour imposer une empreinte obtenue **indépendamment d'un canal de confiance vérifié**, passer `--expected-sha256` à l'updater : il compare le SHA-256 de l'archive complète **avant extraction, sauvegarde ou installation**, y compris en `--dry-run`. Exemple, en remplaçant la valeur par celle validée hors du ZIP téléchargé :

~~~bash
cifereca-update-dev dist/cifereca-plugins-only-preview-manual.zip \
  --expected-sha256 "$SHA256_APPROUVE" --dry-run --json
cifereca-update-dev dist/cifereca-plugins-only-preview-manual.zip \
  --expected-sha256 "$SHA256_APPROUVE" --json
~~~

**Important :** une empreinte recopiée depuis la même archive, un sidecar non authentifié ou un manifeste embarqué n'établit **aucune provenance**. L'option est facultative pour conserver les anciens snapshots de développement.

### Mode strict à utiliser pour une release plugins-only approuvée

Le client qui installe une release plugins-only **doit connaître son contexte de confiance hors du ZIP**. Pour refuser toute rétrogradation silencieuse vers un snapshot dev générique (sans index de provenance), et garantir que les octets extraits sont ceux dont l'empreinte a été approuvée :

~~~bash
cifereca-update-dev dist/cifereca-plugins-only-preview-manual.zip \\
  --require-plugins-only --expected-sha256 "$SHA256_APPROUVE" --dry-run --json
cifereca-update-dev dist/cifereca-plugins-only-preview-manual.zip \\
  --require-plugins-only --expected-sha256 "$SHA256_APPROUVE" --json
~~~

Avec `--require-plugins-only`, le programme refuse **avant toute extraction** l'absence de `--expected-sha256` ; après vérification de la copie privée du ZIP, il exige également le marqueur `scope: plugins-only`, l'index, les versions Core/API et les SHA de packages. Une ancienne archive de développement sans index est **acceptée par défaut** dans le mode historique, mais **refusée** en mode strict. La vérification utilise la **copie privée déjà validée** pour empêcher le remplacement du ZIP source entre le contrôle de SHA et son extraction.

**Limite :** ce mode ne crée pas à lui seul la chaîne de confiance. Le hachage attendu doit venir d'un canal extérieur **authentifié indépendamment** (p. ex. processus de publication vérifié), et non du ZIP ou de son sidecar. Le travail de provenance/signature et de recette sur un artefact GitHub Release officiellement publié reste ouvert (#787 / #577).

## Compatibilité avec le Core cible (précontrôle obligatoire avant installation)

Le contrôle d'intégrité `--verify` ne suffit **pas** à garantir la compatibilité d'une ancienne version Core. Avant d'installer, relever les versions du Core et de la Plugin API **du runtime cible**, puis vérifier :

~~~bash
python -m scripts.package_partial_plugins \
  --verify dist/cifereca-plugins-only-preview-manual.zip \
  --target-core-version '0.9.44+dev.11' \
  --target-plugin-api-version '1.4.0'
~~~

Ces valeurs ne sont que des **exemples**, à remplacer par les versions installées. Les deux options doivent être présentes ensemble : le contrôle refuse un Core différent de celui réellement testé lors de la construction, ou un package incompatible avec la Plugin API cible. Il ne garantit pas la compatibilité avec d'autres versions Core.

Ce précontrôle est également exécuté dans le workflow après installation du wheel courant, avant `cifereca-update-dev --dry-run`. Le garde-fou est aussi intégré à l'updater : version du Core installé, Plugin API, marqueur de portée et index sont vérifiés avant toute mutation. Ce contrôle n'authentifie cependant pas le ZIP et ne remplace pas une provenance de publication fiable : ticket Core [#787](https://github.com/pzia/cifereca/issues/787).

## CI et installation

Le workflow manuel **Preview plugins-only package** accepte une liste d'IDs séparés par des virgules et dépose le ZIP + son SHA-256 parmi les artefacts GitHub Actions. Il exécute le contrôle des sources, les tests de packaging, la vérification de l'index, des sommes de contrôle et cifereca-update-dev --dry-run --json après installation du Core courant.

Pour une installation manuelle hors du pipeline :

~~~bash
cifereca-update-dev <snapshot.zip> --dry-run --json
cifereca-update-dev <snapshot.zip> --json
~~~

L'updater existant effectue la sauvegarde préalable des plugins touchés, restaure en cas d'échec et lance son smoke test. Il préserve l'activation d'un package déjà installé. Les packages non sélectionnés sont inchangés ; aucun tag ou GitHub Release n'est créé par ce workflow.

**Reste hors de ce groupe** : la signature/provenance forte des publications, la politique de tags plugins-only, la compatibilité Core multi-version et son application par l'updater, le contrôle d'upgrade/rollback d'une release complète à une partielle, la traçabilité persistante de la provenance dans l'UI et la publication officielle des artefacts. Ces points doivent être validés avant de déclarer #577 terminé et avant toute release publique.
