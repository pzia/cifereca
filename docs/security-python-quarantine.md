# #2770 — Quarantaine Python : une règle, pip comme résolveur

**Règle** : un nouveau wheel tiers doit avoir été publié sur PyPI depuis au
moins **168 heures UTC**. L'âge s'applique au **fichier exact**, même lorsqu'une
ancienne version reçoit un nouveau wheel. Ne jamais utiliser le mtime ni la
date de publication de la seule version.

## Implémentation Core (première tranche)

L'installation gérée des dépendances de plugins suit trois étapes :

1. `python -m pip --isolated install --dry-run --report PLAN.json
   --no-cache-dir --only-binary=:all: --index-url https://pypi.org/simple ...`
   laisse **pip** déterminer l'ensemble des wheels, y compris les transitives.
2. Le Core confronte chaque URL, nom/version, SHA-256 du rapport et horodatage
   UTC à la fiche **PyPI officielle du même fichier**, sous HTTPS sans miroir
   ni redirection. Un wheel retiré (`yanked`), sans preuve, trop récent ou
   dont le SHA diffère est refusé. Pour un wheel seulement **trop récent**,
   `--constraint nom<version` renvoie la résolution à pip (12 essais maximum).
   Si les contraintes explicites ne permettent aucun candidat ancien, on bloque.
3. `python -m pip --isolated install --no-index --no-deps
   https://files.pythonhosted.org/.../wheel.whl#sha256=...` installe uniquement
   les wheels validées. **Aucune mutation du venv lors du préflight refusé** ;
   les distributions déjà présentes restent installées.

Messages fermés : `dependency_too_recent`,
`dependency_provenance_unavailable`, `dependency_not_eligible`.
La commande existante de réparation conserve sa demande de confirmation.

### Limitations NON résolues de cette tranche

- Le `--dry-run` de pip peut **déjà télécharger des fichiers** pour produire
  son rapport. L'admission garantit la non-installation dans le venv, **pas**
  l'absence de tout téléchargement préliminaire.
- **Wheelhouses/offline/updater et construction CI/release** : une wheelhouse
  sans attestation ne peut pas être supposée admissible du seul fait que son
  contenu est local. Le pipeline de publication doit produire et transporter
  les attestations SHA+date puis les vérifier hors réseau, sans avoir à
  réinterroger PyPI. Tant que ce n'est pas fait, le P0 reste **ouvert**.
- Les dérivations locales du Core ne sont pas soumises à l'âge de leur build.
  Une dépendance de build externe neuve, elle, doit être contrôlée à la
  préparation. Les installations pip manuelles hors flux gérés ne sont pas
  couvertes.
- `pipx` reste une option standard pour isoler une application CLI, mais n'est
  pas une source d'horodatage PyPI et ne remplace pas le contrôle des wheels.
  Aucune implémentation personnalisée de résolveur ou installateur pipx.

Aucun contournement silencieux de la quarantaine n'est autorisé. Les correctifs
urgents nécessitant une dérogation explicite, bornée et auditable restent
également à concevoir dans #2770.

## Préflight hors ligne de l'updater (tranche suivante)

Lorsqu'un répertoire `offline-wheels/` est présent, **avant même la
création du nouveau venv**, l'updater exige `wheelhouse-provenance.json`.
Pour chaque wheel de ce répertoire, il vérifie l'inventaire exhaustif,
l'identité nom/version tirée du nom de fichier, le SHA-256 des octets,
l'origine PyPI déclarée et l'âge UTC **>= 168 h**. Un fichier ajouté,
modifié ou dépourvu de manifeste provoque `dependency_provenance_unavailable`
ou `dependency_not_eligible`, sans pip ni création de venv. La wheel Core
`cifereca` construite localement pour la version attendue est exemptée
du délai de son propre build, **pas** ses dépendances tierces.

Le producteur de wheelhouse, sous **pipeline de confiance**, exécute par
exemple :

```bash
python scripts/attest-wheelhouse.py offline-wheels --core-version "$(cat VERSION)"
```

Cette commande vérifie les métadonnées HTTPS du **fichier exact** sur
PyPI (SHA, horodatage, non-yanked), puis écrit le manifeste complet.
Elle échoue si une transitive, même pour une ancienne version, n'est
pas admissible. Le manifeste doit être **embarqué et protégé avec l'archive
signée** ; une copie JSON isolée, modifiable par l'expéditeur, **n'est pas
une preuve authentique**. Le vérificateur local garantit l'intégrité
relativement à ce manifeste, il ne vérifie pas une signature cryptographique
à lui seul. L'archive doit donc provenir d'une chaîne de confiance
vérifiée en amont (signature de release avant import).

**Toujours ouvert dans #2770** : le script d'attestation inspecte des
wheels déjà téléchargées ; l'admission **avant téléchargement** dans tous
les workflows CI/release n'est pas encore prouvée. L'installation gérée depuis un checkout source **sans** wheelhouse est
maintenant **refusée avant la création du venv** : le repli pip non attesté a
été supprimé. Pour `cifereca-update --github`, le commit GitHub sélectionné
est d'abord extrait dans un espace temporaire ; son producteur
`scripts/prepare-admitted-wheelhouse.py` fabrique une wheelhouse attestée
à partir des dépendances PyPI éligibles, contrôlée localement avant
`install_source`. Si la preuve ne peut pas être obtenue (PyPI indisponible,
aucun candidat admissible, outil de build manquant), l'update échoue **sans
remplacer le Core déjà installé**. `--github --dry-run` n'effectue aucun
téléchargement de wheels. Les ZIP/checkout locaux dépourvus d'une wheelhouse
attestée échouent fermés ; un pipeline de publication doit la préparer. Pour le gate d'intégration `core-full-install.yml`, le script
`scripts/prepare-admitted-wheelhouse.py` résout les dépendances runtime
(transitives comprises), refuse les wheels récentes, réessaie via les contraintes
pip uniquement lorsqu'une version plus ancienne est admissible, puis assemble
et atteste `offline-wheels/` avant le test de l'updater. Son préflight pip
peut encore télécharger des métadonnées ou wheels avant admission : ce point
reste ouvert et ne doit pas être présenté comme une admission avant récupération. La réparation/installation de packages plugins
hors ligne et la dérogation CVE nominative restent à traiter.

## Publication de l'archive Core hors ligne (Linux x86_64 / Python 3.11)

Le pipeline officiel `.github/workflows/release.yml` construit un **quatrième
artefact** `cifereca-offline-<VERSION>-linux-x86_64-py311.zip`, avec les
sources exactes du commit publiable et `offline-wheels/` (incluant le
manifeste de provenance). Le producteur `scripts/prepare-admitted-wheelhouse.py
--include-plugin-deps` résout *ensemble* les dépendances du Core et les
`python-package` de tous les plugins embarqués, ainsi que leurs transitives.
Les conflits ou wheels insuffisamment âgées font **échouer la publication**,
pas l'installation chez l'utilisateur. Le packaging exclut fichiers non
versionnés, liens symboliques et répertoires privés, et reprend les contrôles
d'inventaire, de SHA et d'âge.

Avant même de créer un brouillon GitHub Release, `scripts/ci-offline-release-gate.py`
installe l'archive officielle dans des répertoires XDG **vierges**, avec
`PIP_NO_INDEX=1` et `CIFERECA_PLUGIN_DEPS_OFFLINE=1`, puis contrôle le Core,
les plugins et les trois entrypoints. La CI de pull request rejoue cette
recette sans publication. Le ZIP reçoit un SHA dans `SHA256SUMS` et une
signature Sigstore Cosign vérifiée avant promotion du brouillon.

**Périmètre attesté par ce premier gate : Linux x86_64, Python 3.11, avec les
prérequis système GTK/libadwaita documentés.** Les architectures et versions
Python supplémentaires ne sont pas validées par cette seule matrice. Les
dépendances Python d'outillage de build installées au préalable dans les
runners CI, l'authenticité à la réception chez l'utilisateur et le statut
« plugin optionnel différé » restent ouverts dans #2770. Une release portant
un environnement non validé ne doit pas être présentée comme universellement
installable.
