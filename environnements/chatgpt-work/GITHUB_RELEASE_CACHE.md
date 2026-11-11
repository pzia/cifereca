# Cache GTK4 Ubuntu 24.04 sur GitHub Releases

**Cible exclusive : Ubuntu 24.04 `noble`, amd64, Python 3.12.** Ne jamais réutiliser
l'archive Debian 13 déjà publiée : ses dépendances binaires ne sont pas compatibles.

## Publication

Workflow dédié : [Publish Ubuntu 24.04 GTK4 rootless offline cache](../../.github/workflows/gtk-ubuntu24-cache-asset.yml).
Il doit d'abord être fusionné sur `main`, puis lancé manuellement avec :

- `release_tag=gtk4-ubuntu24-noble-amd64-20261111` (ou une nouvelle date unique) ;
- `confirm_publish=true` après approbation.

Deux jobs : construction de la fermeture des paquets GTK4 sur **Ubuntu 24.04**
avec `chatgpt-work/work_env.py bootstrap` (APT signé), puis validation et publication
d'une **prerelease GitHub immuable**, dans un job séparé sans checkout et avec
`contents:write`. Le seul contenu publié est l'archive de `.deb` filtrés,
le marqueur `{"distro":"ubuntu","codename":"noble","architecture":"amd64"}`
et `SHA256SUMS`. Aucun fichier utilisateur, capture, log, clé ou checkout.

La publication refuse un tag existant. Si APT/DNS est inaccessible sur GitHub
Actions, la construction échoue : **ne jamais annoncer un asset publié** sans
vérification de la Release et de son SHA.

## Asset attendu après publication réussie

- Release : `https://github.com/pzia/cifereca/releases/tag/gtk4-ubuntu24-noble-amd64-20261111`
- Archive : `gtk4-deb-cache-ubuntu24-noble-amd64.tar.gz`
- Somme : `SHA256SUMS`

Ces adresses ne sont **pas une confirmation de disponibilité**. Contrôler que les
deux assets existent avant de les utiliser.

## Installation Ubuntu hors réseau APT

Relever le SHA-256 dans `SHA256SUMS` publié sur la Release et vérifier son
origine, puis :

```bash
cat /etc/os-release
/usr/bin/python3 --version  # 3.12
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/cifereca/gtk4-noble-amd64"
python3 environnements/chatgpt-work/fetch_release_cache.py \
  --sha256 '<SHA256_DE_LA_RELEASE_VERIFIE>' --dest "$CACHE"

mkdir -p environnements/chatgpt-work/runtime/apt/archives
cp "$CACHE"/*.deb environnements/chatgpt-work/runtime/apt/archives/
cd environnements/chatgpt-work
./work bootstrap --offline
./work check-import
```

Le fetch vérifie système/architecture/interpréteur, SHA demandé et SHA256SUMS
de la Release, marqueur, taille, nom et signature `!<arch>` de chaque `.deb`,
refuse les symlinks, l'archive piégée et l'écrasement d'un cache existant.
`./work bootstrap --offline` réutilise ces `.deb` sans installer de paquet système.

Pour GitHub Actions `E2E Smoke`, l'archive est une option de bootstrap après
publication vérifiée : ne pas remplacer un gate échoué par un succès fictif.
Le runner Ubuntu ne peut pas prendre le cache Debian 13. La préparation E2E
doit posséder les chemins de téléchargement sûrs et échouer si l'asset manque.

Voir également [la recette Debian 13](../openai-chat-debian/GITHUB_RELEASE_CACHE.md).
