# OpenAI Chat Debian 13 — recette GTK4 Cifereca

Pour l'usage **E2E UX**, commencer par [le guide transversal](../E2E-UX.md)
(choix de la machine, commande complète, preuves attendues, cas DNS hors service,
distinction entre runtime local rootless et E2E sur runners GitHub standard).


Recette reproductible pour exécuter et tester Cifereca dans un environnement
**OpenAI Chat Debian 13 (trixie) x86_64 / Python 3.13**, sans installation
système.

Le bootstrap télécharge les paquets Debian signés dans un cache local puis les
extrait sous `runtime/root/`. Aucun `sudo`, `apt install` système ou changement
de `/etc/apt` n'est nécessaire.

## Cache .deb prêt à l'emploi : GitHub Releases (prioritaire sur le relais temporaire)

**Agents Debian 13 / Python 3.13 sans accès aux miroirs APT :** utiliser en premier [`GITHUB_RELEASE_CACHE.md`](GITHUB_RELEASE_CACHE.md) : archive GTK4/libadwaita/Xvfb déjà regroupée en **asset GitHub Release stable**, contrôle SHA-256, extraction stricte, puis `bootstrap.py --offline`. Entrée directe depuis la racine du dépôt :

```bash
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/cifereca/gtk4-trixie-amd64-20261020"
/usr/bin/python3 environnements/openai-chat-debian/fetch_release_cache.py --dest "$CACHE"
cd environnements/openai-chat-debian
/usr/bin/python3 bootstrap.py --offline --deb-cache "$CACHE" --python /usr/bin/python3
GTK4_POC_PYTHON=/usr/bin/python3 ./gtk-python check_import.py
```

**Ne pas relancer le téléchargement si le cache existe déjà** : l'outil refuse volontairement d'écraser une destination. Le relais GitHub Actions documenté plus bas sert de **secours/reconstruction**, pas de préalable systématique. Aucune installation système ni archive privée.

## Préparer l'environnement

Prérequis : Debian 13 `trixie`, Python 3.13, `apt-get`, `dpkg-deb`, `tar` et
accès réseau pour le premier bootstrap.

```bash
cd environnements/openai-chat-debian
python3 bootstrap.py
./gtk-python check_import.py
```

Le bootstrap utilise un état APT isolé et télécharge toute la fermeture de
dépendances nécessaire à GTK4/libadwaita, Xvfb, D-Bus et aux dépendances
Python applicatives déclarées actuellement par Cifereca.

Paquets explicitement demandés :

```text
python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1
xvfb xauth libxtst6 python3-pil fonts-dejavu-core
x11-xkb-utils xkb-data dbus-daemon
python3-packaging python3-httpx
```


## Cas OpenAI Chat / conteneur Debian sans DNS sortant

Ce cas est fréquent dans les sessions d'agent : l'hôte est bien Debian 13,
Xvfb/FFmpeg peuvent déjà être présents, mais `apt-get` ne peut pas résoudre
les miroirs Debian. Dans ce cas, la bonne stratégie reste cette recette GTK4.

Diagnostic minimal :

```bash
cat /etc/os-release
python3 -c 'import sys, platform; print(sys.version.split()[0], platform.machine())'
command -v Xvfb || true
command -v ffmpeg || true
./gtk-python check_import.py || true
```

Si `bootstrap.py` échoue sur la résolution DNS, réutiliser un cache Debian 13
portable déjà disponible ou construire un cache via un relais GitHub
ponctuel. Cette démarche concerne seulement **la machine Debian locale**.
L'exécution E2E sur les runners standard Ubuntu 24.04 est autorisée
séparément depuis le 11/10/2026, sans cache Debian transféré.
Pour produire un cache Debian, le conteneur relais doit rester Debian 13
afin de respecter Python 3.13 et les paquets de cette recette.
Le modèle prêt à copier est `github-cache-relay.yml.example` dans ce répertoire.

Séquence de relais recommandée :

```text
branche temporaire GitHub
  -> workflow GitHub Actions
  -> docker run debian:trixie
  -> python3 bootstrap.py --download-only --deb-cache /work/.gtk-deb-cache
  -> actions/upload-artifact
  -> téléchargement de l'artefact via le connecteur GitHub de l'agent
  -> extraction locale
  -> python3 bootstrap.py --offline --deb-cache <cache>
  -> ./gtk-python check_import.py
```

Le sous-répertoire APT `partial/` peut appartenir à root lorsque le cache est
construit dans Docker. Avant `tar`/`upload-artifact`, faire par exemple :

```bash
sudo chown -R "$(id -u):$(id -g)" .gtk-deb-cache
rm -rf .gtk-deb-cache/partial
```

Ne jamais considérer la seule présence de Xvfb ou FFmpeg comme une validation
GTK : `./gtk-python check_import.py` doit réussir avant de lancer une recette
Cifereca.

## Préparer un cache portable

Sur une machine Debian 13 x86_64 connectée :

```bash
python3 bootstrap.py --download-only --deb-cache ./deb-cache
```

Copier `deb-cache/` sur la machine cible puis :

```bash
python3 bootstrap.py --offline --deb-cache ./deb-cache
```

Le cache possède un marqueur `debian/trixie/amd64` pour empêcher le mélange
de paquets d'une autre distribution. `packages-lock.json` est généré localement
avec les versions et SHA-256 effectivement utilisées.

## Lancer Cifereca

```bash
./headless --screen 1300x900x24 -- ./run-cifereca --no-plugins --verbose
```

`run-cifereca` utilise les sources du dépôt (`src/cifereca`) lorsqu'elles sont
présentes. Le lanceur Python `gtk-python` démarre l'interpréteur avec `-S` : les
`site-packages`/`dist-packages` de l'hôte ne peuvent donc pas masquer une
dépendance absente du runtime rootless. Une commande externe peut être imposée
avec `CIFERECA_CMD`.

## Capturer et piloter l'IHM

```bash
./headless --screen 1300x900x24 -- \
  ./capture-app \
    --wait 3 \
    --settle-after-actions 0.5 \
    --out evidence/accueil.png \
    --cwd "$(cd ../.. && pwd)" \
    -- \
    ./run-cifereca --no-plugins --verbose
```

`capture-app` accepte également `--click X,Y`, `--type-text` et `--key` pour
des scénarios X11 reproductibles. Lorsqu’un scénario injecte des actions,
`--settle-after-actions SECONDES` attend explicitement leur stabilisation avant
la capture (valeur entre 0 et 60 s) et enregistre ce délai dans le rapport JSON.


## Démonstration vidéo réelle avec le simulateur

Une vidéo UX doit filmer **la vraie fenêtre GTK Cifereca** sur le display
virtuel. Elle ne doit pas être reconstruite à partir de screenshots, de PIL ou
d'une imitation de l'interface.

Depuis la racine du dépôt, préparer d'abord le profil fonctionnel :

```bash
python3 simulation/datasetctl.py doctor ux-small
python3 simulation/datasetctl.py rebuild ux-small
python3 simulation/datasetctl.py status ux-small
python3 simulation/datasetctl.py summary ux-small
```

Le profil fournit les faux services, les EPUB et les variables XDG. La recette
GTK fournit Xvfb/D-Bus/GTK.

`datasetctl snapshot` cible encore le harnais historique à `.venv/bin/python` ;
pour cette recette Debian 13, utiliser directement `datasetctl exec` avec
`headless` et `capture_app.py`. Exemple de screenshot réel :

```bash
mkdir -p evidence
REPO_ROOT="$PWD"

python3 simulation/datasetctl.py exec ux-small -- \
  env REPO_ROOT="$REPO_ROOT" \
  environnements/openai-chat-debian/headless \
  --screen 1300x850x24 -- \
  "$REPO_ROOT/environnements/openai-chat-debian/gtk-python" \
  "$REPO_ROOT/environnements/openai-chat-debian/capture_app.py" \
  --wait 4 \
  --out "$REPO_ROOT/evidence/ux-small.png" \
  --cwd "$REPO_ROOT" \
  -- \
  python3 "$REPO_ROOT/simulation/datasetctl.py" cifereca ux-small --verbose
```
Pour une vidéo, vérifier d'abord que `ffmpeg` est disponible sur l'hôte. Le
principe est de démarrer FFmpeg **dans le même `headless`** que Cifereca : il
hérite ainsi du même `DISPLAY` et du même `XAUTHORITY`. `capture_app.py` lance
ensuite Cifereca, attend la surface GTK, injecte les actions X11 du scénario et
termine proprement l'application après la capture finale.

Exemple minimal reproductible :

```bash
mkdir -p evidence
REPO_ROOT="$PWD"

python3 simulation/datasetctl.py exec ux-small -- \
  environnements/openai-chat-debian/headless \
  --screen 1300x850x24 -- \
  env REPO_ROOT="$REPO_ROOT" bash -c '
    set -e
    ffmpeg -y \
      -f x11grab -framerate 24 -video_size 1300x850 \
      -i "$DISPLAY" \
      -c:v libx264 -pix_fmt yuv420p \
      "$REPO_ROOT/evidence/ux-small.mp4" &
    recorder=$!
    trap '\''kill -INT "$recorder" 2>/dev/null || true; wait "$recorder" 2>/dev/null || true'\'' EXIT

    "$REPO_ROOT/environnements/openai-chat-debian/gtk-python" \
      "$REPO_ROOT/environnements/openai-chat-debian/capture_app.py" \
      --wait 3 \
      --settle-after-actions 2 \
      --out "$REPO_ROOT/evidence/ux-small-final.png" \
      --cwd "$REPO_ROOT" \
      -- \
      python3 "$REPO_ROOT/simulation/datasetctl.py" cifereca ux-small --verbose
  '
```

Ajouter au besoin `--click X,Y`, `--type-text TEXTE` et `--key TOUCHE` à
`capture_app.py`. Les actions sont alors visibles dans le MP4 puisqu'elles sont
injectées pendant que FFmpeg enregistre le même écran virtuel.

Cette chaîne est importante :

```text
datasetctl exec (XDG + services du profil)
  -> headless (Xvfb + D-Bus + DISPLAY)
  -> ffmpeg x11grab (enregistrement de DISPLAY)
  -> capture_app.py (actions X11 + fin du scénario)
  -> datasetctl cifereca (vraie commande et vrais plugins du profil)
```

Ne pas remplacer `datasetctl cifereca` par un lancement manuel qui oublierait
`--no-plugins` ou les `--force-plugin` dérivés du profil.

Vérifier ensuite le MP4 avec `ffprobe` et conserver les logs Xvfb/D-Bus, le
PNG final et les logs du profil de simulation avec la preuve.
## Validation de la recette

```bash
./gtk-python check_import.py
./headless ./gtk-python test_ui.py
```

`check_import.py` relit les dépendances de `pyproject.toml`, vérifie leurs
contraintes de version et refuse toute distribution résolue hors de
`runtime/root/usr/lib/python3/dist-packages`.

`test_ui.py` pilote l'application GTK de démonstration locale `app.py` ; le
self-test est donc autonome et ne dépend d'aucun fichier applicatif non
versionné.

La recette valide X11/Xvfb et le rendu logiciel Cairo. Elle ne valide pas
Wayland, l'accélération GPU ou un gestionnaire de fenêtres complet.

## Contenu versionné

- `bootstrap.py` : téléchargement/extraction rootless Debian 13 ;
- `env.sh`, `gtk-python` : environnement Python/GTK isolé ;
- `headless`, `headless.py` : Xvfb + D-Bus privés ;
- `capture-app`, `capture_app.py`, `x11_input.py`, `native_capture.py` :
  interaction et capture ;
- `run-cifereca` : lancement du Core depuis les sources du dépôt ;
- `app.py` : application GTK locale et déterministe utilisée par le self-test ;
- `check_import.py`, `test_ui.py` : validations minimales.

`runtime/`, `deb-cache/`, `evidence/`, `packages-lock.json`, les `.deb` et les
fichiers Python compilés sont générés et ignorés par Git.
