# Environnement E2E UX — procédure de référence

**Statut : recette documentée, pas validation de son exécution locale.**
Ce guide est le point d'entrée des agents `ux` pour une **vraie interface GTK4**
et pour la collecte de preuves. Lire ensuite la recette de l'OS :
[`openai-chat-debian/README.md`](openai-chat-debian/README.md) ou
[`chatgpt-work/README.md`](chatgpt-work/README.md).

## 1. Distinguer trois couches

| Couche | Chemin | Ce qu'elle prouve |
| --- | --- | --- |
| Runtime graphique | `environnements/` | GTK4, GI, libadwaita, Xvfb, D-Bus, XTEST et captures fonctionnent |
| État fonctionnel | `simulation/` | Sources simulées, profils, EPUB, configuration et identités isolés |
| Assertions E2E | `e2e/` | Parcours ciblé, gestes, états GTK et traces comparables |

**Un import GTK réussi ne valide pas Cifereca** ; un test du widget d'un
plugin ne certifie pas la fenêtre complète ; une capture n'est pas un PASS
fonctionnel sans assertions.

## 2. Identifier l'hôte, puis choisir UNE recette

Depuis la racine du dépôt :

```bash
cat /etc/os-release
/usr/bin/python3 -c 'import sys,platform; print(sys.version.split()[0],platform.machine())'
command -v Xvfb || true
command -v ffmpeg || true
```

- **Debian 13 `trixie`, Python 3.13, amd64** :
  `environnements/openai-chat-debian/` — runtime **rootless** (pas de
  `apt install` système, pas de `sudo`, pas de `pip install gi`).
- **ChatGPT Work Ubuntu 24.04 `noble`, Python 3.12, amd64** :
  `environnements/chatgpt-work/` — `./work bootstrap` puis
  `./work check-import`, selon son README.
- Sinon : l'hôte n'est pas couvert. Ne jamais mélanger des paquets Ubuntu
  et Debian ni remplacer la vraie interface par une maquette.

Les anciennes archives GTK jointes aux conversations ne sont **pas** des
sources de recette : utiliser uniquement les fichiers versionnés du dépôt.

## 3. Debian 13 : installer et qualifier le runtime local

Depuis la racine du dépôt, **si le réseau Debian est disponible** :

```bash
cd environnements/openai-chat-debian
/usr/bin/python3 bootstrap.py --python /usr/bin/python3
GTK4_POC_PYTHON=/usr/bin/python3 ./gtk-python check_import.py
GTK4_POC_PYTHON=/usr/bin/python3 ./headless \
  --screen 1300x900x24 -- \
  ./gtk-python test_ui.py
cd ../..
```

Les deux contrôles sont distincts :
`check_import.py` vérifie l'origine **rootless** de GI et des bibliothèques
Python ; `test_ui.py` exerce un **app GTK de démonstration du harnais**
(affichage, XTEST, rendu/captures), **pas** Cifereca.

Les chemins `runtime/`, `evidence/`, `packages-lock.json`, cache
`.deb` et artefacts générés ne doivent pas être commités.

**Nouveau chemin le plus court — Debian 13 hors ligne APT :** télécharger le [cache GTK4 Debian 13 sur GitHub Releases](openai-chat-debian/GITHUB_RELEASE_CACHE.md) avec `fetch_release_cache.py`, puis `bootstrap.py --offline --deb-cache <cache>` ; la vérification SHA-256 et du profil est faite avant extraction. Aucun workflow de relais provisoire requis si l'asset Release est accessible.

### Si DNS/APT est inaccessible

La seule solution prévue est un **cache Debian 13 correspondant à la même
architecture**, puis le bootstrap local, jamais l'installation de .deb
Ubuntu dans Debian :

```bash
cd environnements/openai-chat-debian
/usr/bin/python3 bootstrap.py --python /usr/bin/python3 \
  --offline --deb-cache /CHEMIN/VERS/gtk-deb-cache
GTK4_POC_PYTHON=/usr/bin/python3 ./gtk-python check_import.py
```

Le cache doit contenir `.gtk4-cache-profile.json` et les fichiers `.deb`,
et avoir été créé par `bootstrap.py --download-only`. Le fichier de
lock documente versions et SHA-256. Ne pas forcer une dépendance manquante.

**Mise à jour du 11 octobre 2026 :** le propriétaire indique avoir rendu
le dépôt public et levé le blocage budgétaire GitHub Actions. Il **autorise
l'exécution des scénarios E2E GTK sur les runners standard GitHub**.
La suspension antérieure est donc levée pour ces validations, **sans multiplier
les workflows** : utiliser [`E2E Smoke`](../.github/workflows/e2e-smoke.yml)
sur `ubuntu-24.04` avec le venv `--system-site-packages` et les dépendances
GTK4 installées par `scripts/ci-install-gtk-e2e-runtime.sh`.
Les résultats, logs et captures sont publiés comme artefacts ; un PASS
de l'infrastructure n'est pas un PASS des scénarios.

Sur un *hôte Debian 13 local sans DNS*, GitHub peut **séparément** servir de
relais réseau pour les paquets nécessaires au bootstrap rootless, avec
[`openai-chat-debian/github-cache-relay.yml.example`](openai-chat-debian/github-cache-relay.yml.example).
Ce relais est facultatif pour les tests exécutés directement sur GitHub ;
ne pas recréer de workflows concurrents. Voir [issue #1011](https://github.com/pzia/cifereca/issues/1011).

Sans réseau **ni** cache, documenter le blocage : ne pas annoncer
d'installation ou de capture réussie.

## 4. E2E sur la vraie application et les services simulés

Depuis la racine du dépôt, choisir un profil (ex. `ux-small`) et
vérifier les données **avant** la capture :

```bash
python3 simulation/datasetctl.py doctor ux-small
python3 simulation/datasetctl.py rebuild ux-small
python3 simulation/datasetctl.py status ux-small
python3 simulation/datasetctl.py summary ux-small
```

Le chemin éprouvé est **`datasetctl exec` → `headless` →
`capture_app.py` → vraie commande Cifereca**. Reprendre la commande
complète, y compris les `--force-plugin` issus du profil, dans
[`openai-chat-debian/README.md`](openai-chat-debian/README.md#démonstration-vidéo-réelle-avec-le-simulateur).
Pour une vidéo, FFmpeg `x11grab` enregistre le **même DISPLAY** que
Cifereca ; ne pas assembler une vidéo à partir de PNG.

La fonction `datasetctl snapshot` cible encore l'ancien harnais à
`.venv` : ne pas la confondre avec la recette Debian 13 rootless.

## 5. Tests `e2e/` : runners GitHub, Python système et runtime rootless

Le runner [`e2e/run.py`](../e2e/run.py) construit un HOME/XDG
**temporaire par scénario**, utilise `xvfb-run`, lance les probes
versionnés et écrit `e2e-artifacts/<scenario>/`.
Voir [`e2e/README.md`](../e2e/README.md).

Avec un **Python système possédant déjà GI** et les dépendances GTK
(exemple d'un poste préparé), l'entrée est :

```bash
/usr/bin/python3 e2e/run.py --python /usr/bin/python3 \
  --scenario diagnostics-1366
```

**Point technique :** `e2e/run.py` remplace `PYTHONPATH` par `src/`
dans le sous-processus. Sur un runtime local rootless, transmettre le
lanceur `gtk-python` comme `--python` rétablit toutefois
`env.sh` **dans le sous-processus**, après ce remplacement. Vérifier les
imports et le PASS réel avant de conclure. Sur le runner Ubuntu standard,
le venv `--system-site-packages` rend `gi` accessible sans ce lanceur :

```bash
CIFERECA_E2E_PYTHON=/CHEMIN/DU/VENV/bin/python \
  dbus-run-session -- /usr/bin/python3 e2e/run.py --scenario diagnostics-1366
```

Ce chemin de validation est implémenté par le workflow `E2E Smoke`.

### Recette immédiate du widget Diagnostics avec le harnais rootless

Une fois `bootstrap.py` et les contrôles du §3 réussis, depuis la
**racine du dépôt** :

```bash
REPO_ROOT="$PWD"
mkdir -p "$REPO_ROOT/e2e-artifacts/diagnostics-1366"
export GTK4_POC_PYTHON=/usr/bin/python3
PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$REPO_ROOT/environnements/openai-chat-debian/headless" \
  --screen 1366x900x24 -- \
  "$REPO_ROOT/environnements/openai-chat-debian/gtk-python" \
  "$REPO_ROOT/e2e/diagnostics_page_probe.py" \
  --out "$REPO_ROOT/e2e-artifacts/diagnostics-1366" --width 1366
```

Cette commande contourne la perte de `PYTHONPATH` du runner, en utilisant
les lanceurs **versionnés** `headless` et `gtk-python`.
Elle lance une **fenêtre GTK contenant le widget réel du plugin**
avec brokers simulés ; **elle ne couvre pas la navigation réelle depuis
la fenêtre Cifereca**. Le probe produit notamment `screen.json`,
`screen.png` et les captures des étapes. **Cette invocation directe
n'écrit pas `e2e-artifacts/summary.json`** (fichier appartenant au runner).
La commande est issue de la lecture du code, non exécutée ici : vérifier
le verdict `PASS` et les captures avant toute conclusion.

## 6. Preuves, verdicts, échecs et responsabilités

Conserver : nom du scénario/profil, SHA Git, OS et interpréteur,
commande effective, statut/PASS-FAIL, `screen.json`, PNG et empreintes,
`stdout.log`/`stderr.log` et logs Xvfb/D-Bus. Ne pas diffuser
d'identités, comptes, cookies ou données réelles.

- **Bootstrap KO / import GI KO** : défaut du runtime, aucune preuve UX.
- **`check_import` OK seulement** : capacité d'import, pas de fenêtre.
- **`test_ui.py` PASS** : infrastructure GTK/XTEST, pas de parcours métier.
- **Probe widget PASS** : widget ciblé uniquement, services simulés.
- **Parcours Cifereca PASS** : vraie application et interactions attestées,
  dans les limites du scénario observé.
- **Blocage budgétaire GitHub** : ne pas relancer ; conserver `bloqué`,
  sans le transformer en FAIL applicatif.

Le rôle `ux` appartient aux parcours, au simulateur exploratoire et
aux preuves ; il **ne corrige pas le Core/plugins/CI-CD**.
Transmettre tout défaut produit au propriétaire correspondant et tout
défaut d'outillage CI au rôle `cicd`.
