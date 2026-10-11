# PyGObject GTK4 — Debian 13 (recette UX locale)

## Pourquoi `import gi` échoue

Le runner de cette session utilise `/opt/pyvenv/bin/python3`. Debian installe
`python3-gi` pour le **Python système** (`/usr/bin/python3`), pas
automatiquement dans le venv. Installer PyGObject uniquement avec pip dans
un venv sans entêtes GTK/PkgConfig n'est pas la solution recommandée.

## Installation (machine Debian 13 ayant accès au dépôt APT)

```sh
sudo bash environnements/setup-gtk4-e2e-debian13.sh
```

Le script installe PyGObject, GI GTK4/libadwaita, PIL, Xvfb/Xauth et XTEST
dans Debian, puis **vérifie réellement** l'import `gi`, `Gtk`, `Adw`
et `PIL` avec `/usr/bin/python3`.

## Vérifier la recette Diagnostics

```sh
/usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk; print(Gtk.get_major_version())'
/usr/bin/python3 e2e/run.py --python /usr/bin/python3 --scenario diagnostics-1366
```

Les sorties du scénario vont dans `e2e-artifacts/diagnostics-1366/` et
son `screen.json` doit explicitement indiquer `PASS`.

**Attention :** la présence de `gi` ne garantit pas la réussite du probe.
La recette GTK reste ouverte jusqu'à obtention d'un rapport et captures
validés. Si `xvfb-run` ou l'import de Cifereca échoue, installer les
dépendances Python du projet selon la documentation du dépôt ; ne pas
remplacer le Python système par un venv incompatible.

Dans le conteneur actuel, le dépôt APT est inaccessible (mise à jour expirée
sur délai réseau) : les paquets ne peuvent pas être installés ici. Aucune
installation réussie ni capture GTK n'est revendiquée.

Aucun workflow GitHub Actions n'est ajouté.

## Alternative demandée : dépôts officiels Ubuntu

Pour Ubuntu **24.04 LTS** (*noble*), un second script installe PyGObject
depuis les dépôts Ubuntu configurés **dans cet OS** :

```bash
sudo bash environnements/setup-gtk4-e2e-ubuntu2404.sh
/usr/bin/python3 e2e/run.py --python /usr/bin/python3 --scenario diagnostics-1366
```

Le script **refuse** de s'exécuter sur Debian : installer des paquets Ubuntu
directement dans Debian 13 peut casser les ABI GTK/GObject et le gestionnaire
APT. Utiliser un hôte Ubuntu, une VM ou un conteneur Ubuntu 24.04 disposant
d'un affichage Xvfb et d'un accès réseau. Le conteneur actif est Debian 13,
sans Docker/Podman/Proot et sans résolution DNS même vers
`archive.ubuntu.com` ; le script Ubuntu n'a donc pas été exécuté ici.
