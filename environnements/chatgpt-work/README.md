# VM ChatGPT Work / Ubuntu — tests GTK4 Cifereca

Recette reproductible pour tester la vraie IHM GTK4/libadwaita de Cifereca dans une VM **ChatGPT Work Ubuntu 24.04 x86_64**, sans bureau et sans installation système.

Aucun paquet .deb n'est versionné. Le bootstrap télécharge les paquets Ubuntu signés dans runtime/apt/archives/, les extrait sous runtime/root/ et crée .venv/. Ces répertoires sont ignorés par Git.

## Préparer la VM

Prérequis : Ubuntu 24.04 (noble), Python 3.12 + venv, Bash, apt-get, dpkg-deb, tar, accès Internet au premier lancement et sockets TCP locales autorisées.

    cd environnements/chatgpt-work
    ./work bootstrap
    ./work check-import

check-import doit afficher gtk_import: OK, adwaita_import: OK et display: null.

Le bootstrap est rootless : pas de sudo, pas de apt install et aucune modification de /etc/apt. Il utilise le snapshot APT de la VM s'il existe, sinon les dépôts Ubuntu noble officiels. Pour réutiliser le cache local sans réseau :

    ./work bootstrap --offline

## Lancer Cifereca sans écran physique

    ./work headless --screen 1300x900x24 -- ./run-cifereca --no-plugins --verbose

run-cifereca lance en priorité les sources du dépôt si src/cifereca/app.py existe, sinon une commande cifereca installée. On peut forcer un binaire :

    CIFERECA_CMD=/chemin/vers/cifereca ./work headless -- ./run-cifereca --verbose

Pour limiter le coût du startup pendant une recette UX :

    # Core seul
    ./work headless -- ./run-cifereca --no-plugins --verbose

    # Core + un plugin ciblé (Core 0.9.44)
    ./work headless -- ./run-cifereca --no-plugins --force-plugin <plugin-id> --verbose

Les répertoires XDG et le HOME utilisés par Cifereca sont isolés sous environnements/chatgpt-work/runtime/. Le ~/EBooks par défaut devient runtime/home/EBooks.

## Capturer un snapshot

    ./work capture \
      --screen 1300x900x24 \
      --wait 4 \
      --out evidence/accueil.png \
      --cwd "$(cd ../.. && pwd)" \
      -- \
      ./run-cifereca --no-plugins --verbose

La commande produit evidence/accueil.png, evidence/accueil.json, evidence/xvfb.log et evidence/dbus.log. Le rapport JSON contient les dimensions, le SHA-256, les actions injectées et confirme que le processus était encore ouvert au moment de la capture.

## Interagir avec l'IHM

Les clics utilisent des coordonnées X11 absolues dans l'écran virtuel. Faire d'abord une capture, relever les coordonnées, puis rejouer :

    ./work capture \
      --screen 1300x900x24 \
      --click 420,240 \
      --type-text "tour" \
      --key Return \
      --out evidence/recherche-tour.png \
      --cwd "$(cd ../.. && pwd)" \
      -- \
      ./run-cifereca --no-plugins --verbose

--click X,Y et --key sont répétables. --type-text accepte de l'ASCII imprimable. Les événements sont envoyés via XTEST.

## Boucle UX recommandée

1. ./work reset si un état vierge est nécessaire ;
2. préparer les données dans runtime/home/EBooks et/ou démarrer les pseudo-services sous gen-dataset/ ;
3. lancer d'abord --no-plugins, puis --force-plugin <id> si le scénario cible un plugin ;
4. capturer en 1300×900 ou à la largeur explicitement testée ;
5. inspecter le PNG et runtime/state/cifereca/cifereca.log ;
6. modifier, relancer, recapturer sous un nouveau nom.

Exemple :

    ./work capture --out evidence/01-avant.png -- ./run-cifereca --no-plugins --verbose
    # ... modification ...
    ./work capture --out evidence/02-apres.png -- ./run-cifereca --no-plugins --verbose

## Tests Python

Après source ./env.sh, le Python du venv est .venv/bin/python :

    source ./env.sh
    .venv/bin/python -m pytest -q

Pour un test qui ouvre réellement GTK :

    ./work headless --screen 1300x900x24 -- .venv/bin/python -m pytest -q tests/<test_cible>.py

## Contenu

- README.md : recette précise ;
- work : point d'entrée bootstrap, check-import, headless, capture, reset ;
- work_env.py : téléchargement/extraction rootless, Xvfb, D-Bus, XTEST et capture PNG ;
- env.sh : GTK/libadwaita + XDG isolé + rendu Cairo/X11 ;
- run-cifereca : lance Cifereca avec HOME/XDG isolés ;
- .gitignore : exclut le venv, le runtime, les .deb téléchargés et les captures.

## Limites

Cette recette valide X11 + Xvfb + rendu logiciel Cairo. Elle ne valide ni Wayland, ni GPU, ni gestionnaire de fenêtres complet, ni matériel réel. En cas d'échec, consulter evidence/xvfb.log et evidence/dbus.log.
