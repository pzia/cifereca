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
