# Releases officielles plugins-only — provenance P0

La publication d'une release plugins-only exige **une commande explicite**. Le
workflow `.github/workflows/plugins-only-release.yml` reste limité à `main` et
se lance soit manuellement (`workflow_dispatch`), soit par une modification
auditée, autorisée et fusionnée de
`.github/plugins-only-release-request.json`. Un push ordinaire ne déclenche pas
de release. La publication n'installe ni ne publie un nouveau Core.

## Dépendance obligatoire : Core réellement publié

Une release plugins-only ciblant `v<CORE_VERSION>` est **bloquée** tant
que la release Core officielle `v<CORE_VERSION>` n'existe pas et n'est
pas publiée. Avant de signer le ZIP, le workflow télécharge depuis la
GitHub Release le **wheel réellement distribué** et `SHA256SUMS`,
vérifie l'empreinte SHA-256 côté GitHub et dans le sidecar, recoupe le
SHA du commit du tag, installe ce wheel isolément et vérifie la présence
des options strictes de `cifereca-update-dev`.

Les recettes d'installation, de refus et de rollback utilisent ensuite
les **sources du tag Core publié**, et non les seules sources courantes
de `main`. Le workflow PR, qui ne publie rien, teste toujours le Core
de la branche en préparation. Une version textuellement identique ne
suffit plus à autoriser la publication.

La release Core `v0.9.44+dev.12` est publiée depuis le 09/10/2026,
mais cela ne prouve pas son installation chez l'utilisateur : le workflow
doit toujours en télécharger et vérifier le **wheel exact** avant toute
nouvelle publication plugins-only. Ne jamais réécrire une release publiée
ni son tag, notamment `v0.9.44+dev.11` et `v0.9.44+dev.12`.

## Signature et confidentialité

**Le dépôt `pzia/cifereca` est public. Les releases et leurs assets ZIP
sont accessibles publiquement ; aucune archive, fixture, capture, URL signée,
donnée XDG ou profil utilisateur réel ne doit y être déposé.** Les archives
de prévisualisation Actions suivent aussi une liste positive de chemins et
une rétention courte (voir #1143).

Le workflow officiel utilise **Cosign keyless** (Sigstore) : certificat émis
sur l'identité OIDC du workflow GitHub sur `main`, preuve de transparence
publique et bundle de signature `<tag>.zip.sigstore.json`. Ce mécanisme
reste indépendant d'une éventuelle GitHub Artifact Attestation : seul un
contrôle cryptographique des octets du ZIP, des claims OIDC et du SHA exact
constitue la preuve acceptée par l'updater. L'empreinte, l'identité du
signataire et les métadonnées Sigstore/Rekor peuvent aussi être publiques.

Le SHA-256 sidecar seul ne constitue **pas** une signature. La vérification Cosign
contrôle l'identité exacte du workflow, le dépôt, la branche `main` et le SHA de
source. La signature porte sur les **octets exacts du ZIP**.

## Contrôles obligatoires avant publication

1. Périmètre plugins-only, sélection, versions Core exacte et Plugin API.
2. Installation stricte, refus sans mutation sur Core incompatible (dont ancien
   Core réel `v0.9.44+dev.10`), conservation des états et rollback.
3. Signature du ZIP avec `cosign sign-blob --yes` et création du bundle Sigstore.
4. Vérification indépendante de la signature, du SHA et des claims workflow/ref/SHA.
5. Création de la GitHub Release **en brouillon seulement**.
6. Téléchargement des vrais assets depuis ce brouillon, re-vérification du
   SHA, de la signature et nouveau passage des gates de runtime et rollback.
7. Publication seulement si toutes ces validations aboutissent.

Si une étape échoue, **ne pas publier**. Les assets d'une release déjà publiée
ne peuvent pas être remplacés par un rerun ; utiliser un nouveau tag.

## Vérification par le destinataire

Prérequis : `gh` pouvant accéder à la release publique, `cosign` récent,
`sha256sum`, `cifereca-update-dev` disposant du mode strict.

```bash
repo=pzia/cifereca
tag="plugins-v0.9.44+dev.11-001"
expected_commit="<SHA_40_HEX_DU_RUN_OFFICIEL>"
workflow="https://github.com/$repo/.github/workflows/plugins-only-release.yml@refs/heads/main"

gh release download "$tag" --repo "$repo" \
  --pattern "$tag.zip" \
  --pattern "$tag.zip.sha256" \
  --pattern "$tag.zip.sigstore.json"

cosign verify-blob "$tag.zip" \
  --bundle "$tag.zip.sigstore.json" \
  --certificate-identity "$workflow" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  --certificate-github-workflow-repository "$repo" \
  --certificate-github-workflow-ref "refs/heads/main" \
  --certificate-github-workflow-sha "$expected_commit"

sha256sum -c "$tag.zip.sha256"
verified_digest="$(sha256sum "$tag.zip" | awk '{print $1}')"
cifereca-update-dev "$tag.zip" \
  --require-plugins-only --expected-sha256 "$verified_digest" \
  --dry-run --json

# Après vérification du Core installé et approbation :
# cifereca-update-dev "$tag.zip" \
#   --require-plugins-only --expected-sha256 "$verified_digest" --json
```

L'updater **n'effectue pas d'appel réseau implicite** pour vérifier Cosign.
La vérification de provenance est la responsabilité du canal de distribution
et doit précéder l'installation. L'index plugins-only et les SHA internes sont
validés avant toute mutation.

## Conditions supplémentaires pour fermer P0 #787 / #839

Une CI de PR verte n'est pas une preuve de release. Il faut l'asset officiel
publié, le bundle Sigstore, les contrôles téléchargés, et les logs d'installation
compatible/incompatible/rollback avec intégrité du magasin et des données XDG.

**Attention :** la version Core `0.9.44+dev.11` a été conservée sur `main`
malgré des modifications survenues depuis le tag Core officiel du même numéro.
Une égalité de version seule ne prouve pas l'identité des octets du Core.
Tester le Core **réellement distribué** ; s'il ne dispose pas du mode strict,
la recette de fermeture globale #839 nécessite une publication Core distincte,
sur autorisation explicite. Ne pas clore P0 par déduction.

## Reçu local de dernière mise à jour (#577)

Après **installation réelle**, validation de l'index, contrôle des packages et
smoke réussi, `cifereca-update-dev` écrit pour chaque package sélectionné
`<plugin-store>/<package-id>/.cifereca-last-plugin-update.json` **dans la
transaction sauvegardée**. Un échec de smoke, de vérification ou d'écriture
provoque le rollback du package et de son reçu. Aucun reçu n'est produit
par `--dry-run` ou pour les packages non sélectionnés.

Le reçu borne et valide les champs `scope`, `version`, `snapshot_id`,
`source_git_commit`, `core_version_tested`, SHA-256 du snapshot et du
package. Il est visible via `package_inventory.last_plugin_update` et
`diagnostic.last_plugin_update` (sans identifiant de compte ni chemin
local). Le lecteur refuse les symlinks, JSON anormal et les versions qui
ne figurent plus dans l'inventaire installé.

`trusted_digest_verified` signifie **uniquement** que les octets du ZIP
correspondaient au SHA fourni au CLI. Le reçu local ne prouve ni que la
signature **Cosign a été vérifiée**, ni que la GUI courante a rechargé le
package ; `signature_verified: false` est explicite. Le tag/identité
OIDC et le bundle Sigstore doivent toujours être vérifiés indépendamment
**avant l'installation**, comme décrit plus haut. La GUI doit être
redémarrée et inspectée dans le même HOME/XDG pour attester la version
réellement chargée. Les installations antérieures à ce mécanisme exposent
`last_plugin_update: null`, sans fausse provenance reconstituée.

Les tests CI couvrent un et plusieurs packages, statut désactivé conservé,
refus strict, dry-run, rollback et tampering du reçu. **Aucune release
automatique n'est déclenchée par cette évolution.**
