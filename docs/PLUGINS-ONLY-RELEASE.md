# Releases officielles plugins-only — contrat de confiance P0

Une release plugins-only est publiée **uniquement sur ordre explicite**, via
`.github/workflows/plugins-only-release.yml` déclenché sur `main`.
Un snapshot de prévisualisation ou un simple ZIP accompagné de son SHA-256
**ne prouve pas son origine**.

## Publication (CI/CD)

Avant toute création de brouillon GitHub Release ou upload d'assets, le workflow :

1. valide l'identifiant de release et la branche `main` ;
2. reconstruit la sélection de packages, contrôle l'index, les SHA-256 et la compatibilité **Core exact + Plugin API** ;
3. exerce l'installation stricte réelle, le refus d'un Core divergent et le rollback ;
4. signe le ZIP avec `actions/attest@v4` (attestation SLSA) ;
5. **revérifie l'attestation** des octets exacts avec `gh attestation verify`, en imposant le dépôt, le workflow signataire, `refs/heads/main` et le SHA du commit source.

Une erreur à n'importe quelle étape **interdit la publication**. Le sidecar
`.sha256` ne constitue pas une signature.

## Vérification par le destinataire

Télécharger le ZIP et son sidecar depuis la release GitHub, puis vérifier le ZIP
*avant* de lancer `cifereca-update-dev`. Utiliser le **SHA de commit attendu de
la release que vous avez vérifiée** (pas un SHA déclaré par le ZIP lui-même) :

```bash
repo=pzia/cifereca
tag="plugins-v<VERSION_CORE>-<SERIE>"
expected_commit="<SHA_40_HEX_DE_MAIN_ATTENDU>"

gh release download "$tag" --repo "$repo" \
  --pattern "$tag.zip" --pattern "$tag.zip.sha256"

gh attestation verify "$tag.zip" \
  --repo "$repo" \
  --signer-workflow "$repo/.github/workflows/plugins-only-release.yml" \
  --source-ref refs/heads/main \
  --source-digest "$expected_commit"

sha256sum -c "$tag.zip.sha256"
verified_digest="$(sha256sum "$tag.zip" | awk '{print $1}')"

cifereca-update-dev "$tag.zip" \
  --require-plugins-only --expected-sha256 "$verified_digest" \
  --dry-run --json
# Après examen du rapport et approbation explicite :
# cifereca-update-dev "$tag.zip" \
#   --require-plugins-only --expected-sha256 "$verified_digest" --json
```

L'index `cifereca-plugins-only.json` et les empreintes internes sont vérifiés
par l'updater **avant mutation**, et la compatibilité est volontairement
**exacte** avec la version Core déclarée comme testée. Les snapshots dev
historiques sans marqueur ne sont pas promus en releases officielles. L'updater
ne récupère pas d'attestation via le réseau : la vérification de signature et
d'identité du signataire incombe au canal de distribution/à l'opérateur.

## Limites

Les tests de PR valident le packaging, le refus en cas d'incompatibilité et le
rollback, mais **ne signent pas et ne publient pas** de release officielle.
La validation d'une attestation réellement produite requiert une exécution
manuelle autorisée du workflow de publication ; elle ne peut être revendiquée
avant un run de release réussi. Ne pas transformer un contrôle CI vert en
autorisation de publication.
