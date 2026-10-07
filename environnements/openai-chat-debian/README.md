# OpenAI Chat Debian 13 — recette GTK4 Cifereca

Recette reproductible pour exécuter et tester Cifereca dans un environnement
**OpenAI Chat Debian 13 (trixie) x86_64 / Python 3.13**, sans installation
système.

Le bootstrap télécharge les paquets Debian signés dans un cache local puis les
extrait sous `runtime/root/`. Aucun `sudo`, `apt install` système ou changement
de `/etc/apt` n'est nécessaire.

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
    --out evidence/accueil.png \
    --cwd "$(cd ../.. && pwd)" \
    -- \
    ./run-cifereca --no-plugins --verbose
```

`capture-app` accepte également `--click X,Y`, `--type-text` et `--key` pour
des scénarios X11 reproductibles.

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
