# Cache GTK4 Debian 13 sur GitHub Releases — entrée directe pour les agents

**Pour les agents UX/E2E opérant sur Debian 13 `trixie`, x86_64, Python 3.13.**
Ce cache est un **asset binaire de GitHub Releases**, indépendant du dépôt Git.
Il évite de recréer un relais APT temporaire quand le conteneur ne peut pas
résoudre les miroirs Debian. Il contient uniquement les `.deb` nécessaires au
GTK4/libadwaita/PyGObject, Xvfb, XTEST, D-Bus et aux dépendances Python du
bootstrap rootless — **ni fichier utilisateur, ni code Cifereca, ni capture**.

## Asset stable

- Publication : [Release `gtk4-debian13-trixie-amd64-20261020`](https://github.com/pzia/cifereca/releases/tag/gtk4-debian13-trixie-amd64-20261020)
- Archive : [`gtk4-deb-cache-debian13-trixie-amd64.tar.gz`](https://github.com/pzia/cifereca/releases/download/gtk4-debian13-trixie-amd64-20261020/gtk4-deb-cache-debian13-trixie-amd64.tar.gz)
- Contrôle d'intégrité : [`SHA256SUMS`](https://github.com/pzia/cifereca/releases/download/gtk4-debian13-trixie-amd64-20261020/SHA256SUMS)

Le nom de tag et d'asset est **figé**. Une nouvelle révision doit utiliser
un **nouveau tag**, sans écraser le lot précédent. La Release est marquée
`prerelease` pour **ne pas devenir la dernière version de Cifereca**.

## Commandes recommandées (depuis la racine du dépôt)

```bash
# Vérifier la distribution et l'interpréteur avant le téléchargement.
cat /etc/os-release
/usr/bin/python3 --version

# Télécharger la Release, vérifier SHA-256 et extraire seulement les .deb
# autorisés dans un NOUVEAU répertoire (jamais un cache existant).
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/cifereca/gtk4-trixie-amd64-20261020"
/usr/bin/python3 environnements/openai-chat-debian/fetch_release_cache.py \
  --dest "$CACHE"

# Installer dans runtime/root isolé, sans sudo ni installation système.
cd environnements/openai-chat-debian
/usr/bin/python3 bootstrap.py --python /usr/bin/python3 \
  --offline --deb-cache "$CACHE"
GTK4_POC_PYTHON=/usr/bin/python3 ./gtk-python check_import.py
GTK4_POC_PYTHON=/usr/bin/python3 ./headless \
  --screen 1300x900x24 -- ./gtk-python test_ui.py
```

**Sur une seconde utilisation**, ne pas relancer le téléchargement au même
`--dest` (l'outil refuse d'écraser le cache). Réutiliser simplement les deux
dernières étapes de bootstrap/test avec le répertoire déjà vérifié.
Le runtime créé reste sous `environnements/openai-chat-debian/runtime/`
et le cache utilisateur reste hors Git.

**Si cette VM n'a pas accès à GitHub non plus** : télécharger l'archive et
`SHA256SUMS` sur un environnement connecté autorisé, transférer uniquement
l'archive Debian, puis relever le SHA de la Release et exécuter :

```bash
/usr/bin/python3 environnements/openai-chat-debian/fetch_release_cache.py \
  --archive /chemin/local/gtk4-deb-cache-debian13-trixie-amd64.tar.gz \
  --sha256 "<SHA256_64_CARACTERES_VERIFIE_SUR_LA_RELEASE>" \
  --dest /chemin/vers/un/nouveau/cache
```

Le script refuse : mauvais système/architecture/interpréteur, checksum erroné,
archive trop grande, traversée de chemins, symlinks, types TAR inattendus,
fichiers non-`.deb`, mauvais profil Debian ou cache préexistant. Il ne
demande pas de permissions root et n'envoie rien vers GitHub.
Il ne vérifie **pas** à lui seul la provenance cryptographique de l'archive
Debian : celle-ci dépend de la chaîne APT signée qui a généré les paquets.
Le bootstrap produit `packages-lock.json` avec les versions et SHA de
chaque paquet effectivement installé.

## Production / maintenance de cet asset (rôle CI/CD)

La publication est réalisée par le workflow
[`gtk-debian13-cache-asset.yml`](../../.github/workflows/gtk-debian13-cache-asset.yml)
avec un job de génération **en lecture seule** dans `debian:trixie` :

1. `bootstrap.py --download-only` utilise les dépôts Debian signés ;
2. normalisation contrôlée des noms contenant des *epochs* APT ;
3. `scripts/ci-validate-gtk-relay-cache.py` vérifie une allowlist stricte,
   le marqueur de plateforme, les signatures de format et les tailles ;
4. seul le TAR des `.deb` et `SHA256SUMS` est transmis au job de Release ;
5. le job de publication possède seul `contents:write`, sans checkout de
   la branche, vérifie le SHA et **refuse toute Release existante**.

La fermeture de dépendances vient de Debian : des correctifs de sécurité
peuvent changer la sélection de versions lors d'une reconstruction.
Créer une nouvelle Release datée, conserver le lot précédent si encore
utile, puis ajuster le tag dans `fetch_release_cache.py` et ce guide.
Le contenu de l'asset n'est ni un système Debian complet, ni un bundle
d'applications ni un artefact d'exécution utilisateur. Le miroir Debian
fournit les sources et mentions de licences des paquets redistribués.

## Périmètre et preuve UX

- **Debian 13 trixie amd64, Python 3.13 seulement** : jamais Debian/Ubuntu
  mélangés ; pour Ubuntu ChatGPT Work, suivre
  [`chatgpt-work/README.md`](../chatgpt-work/README.md).
- Un `check_import` réussi qualifie l'environnement, et
  `test_ui.py` qualifie Xvfb/XTEST sur un mini programme ; **ni l'un ni
  l'autre ne valide la vraie application**.
- Pour les captures utilisateur : utiliser ensuite
  [`E2E-UX.md`](../E2E-UX.md), la simulation `datasetctl`, puis la
  vraie fenêtre Cifereca et enregistrer les PNG/vidéos **localement**.
  Ne jamais publier captures, logs bruts, chemins ou comptes privés en
  GitHub Actions (#1143).
- Si GitHub Actions GTK standard est disponible, continuer à préférer
  `E2E Smoke` pour les tests sélectionnés ; ce cache sert avant tout aux
  **VM d'agents Debian à réseau APT isolé**.

Voir également [`environnements/README.md`](../README.md) et
[`environnements/openai-chat-debian/README.md`](README.md).
