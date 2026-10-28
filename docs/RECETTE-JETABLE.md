# Recette jetable de main — #3607

Depuis le dépôt, sur Linux :

```sh
python3 scripts/recette.py
python3 scripts/recette.py --config /chemin/config.toml --no-start
python3 scripts/recette.py --ref COMMIT_SHA_COMPLET --target ~/cifereca-recettes/mon-test --no-start
python3 scripts/recette.py verify ~/cifereca-recettes/mon-test
~/cifereca-recettes/mon-test/run.sh
~/cifereca-recettes/mon-test/stop.sh
python3 scripts/recette.py destroy ~/cifereca-recettes/mon-test
```

Prérequis : Git, Python système ≥3.11 avec `venv` et pip, Bubblewrap avec
namespaces utilisateur disponibles, GTK4/libadwaita/PyGObject et
`dbus-run-session`. Exemple Debian/Ubuntu : paquets `git python3-venv
bubblewrap dbus-x11 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`. L'affichage utilise
un serveur X11 local (`DISPLAY=:N`), y compris XWayland. Aucun port serveur
Cifereca n'est ouvert : l'application actuelle est GTK et son bus D-Bus est
privé. Un bureau Wayland sans XWayland ou une plateforme autre que Linux sont
refusés. Aucun lancement sans confinement ne remplace un Bubblewrap défaillant.

Le script essaie `~/.config/cifereca/config.toml`, puis le format actuellement
utilisé par le Core, `$XDG_CONFIG_HOME/cifereca/config.json` (par défaut
`~/.config/cifereca/config.json`). `--config` choisit strictement le fichier
indiqué, TOML ou JSON ; une erreur de lecture n'ouvre pas une configuration
neuve par défaut. La configuration produite est toujours le JSON attendu par
le Core. Le script n'importe jamais `Config` sur le fichier de production,
car cette classe peut changer ses permissions à l'ouverture.

Chaque cible est un **nouvel enfant direct** de `~/cifereca-recettes/`, racine
privée 0700 appartenant à l'utilisateur. Une cible préexistante, un alias ou
une cible extérieure sont refusés, même avec `--target`. Ni écrasement ni
fusion avec une installation existante. `destroy` vérifie la racine, le
marqueur, l'identité du processus et le verrou avant suppression.
Une préparation encore active garde ce verrou : `destroy` la refuse plutôt
que de supprimer un dossier qu'un copieur pourrait recréer. Interrompre d'abord
la commande de préparation, puis lancer `destroy`. Une préparation interrompue
ou en échec reste supprimable ; une recette lancée est arrêtée automatiquement.

## Contenu et preuve d'intégrité

- Copie physique de la bibliothèque, des données applicatives SQLite et des
  configurations nécessaires ; références de chemins dans les colonnes SQL et
  objets JSON réécrites, y compris les références externes découvertes. Les
  profils/identités et leurs identifiants métier sont conservés pour tester
  les données multicomptes ; ils ne sont pas rebaptisés arbitrairement.
- Lecture des sources avec `O_NOATIME`, sans changement de permissions ; liens
  symboliques et fichiers spéciaux refusés. Une source inaccessible sans
  modifier son atime provoque un refus, notamment un fichier appartenant à un
  autre utilisateur sans permission `O_NOATIME`.
- SQLite : copie privée de l'ensemble base/WAL/SHM/journal entre deux
  observations identiques (empreintes et métadonnées), puis **SQLite Backup
  API** sur cet ensemble. Les transactions WAL validées sont présentes. Aucun
  SQLite n'ouvre la production : même `mode=ro` pourrait modifier/créer un SHM.
  Après cinq tentatives instables, refus explicite ; réessayer dans une période
  moins active. L'empreinte finale doit encore correspondre à la capture.
- Fetch HTTPS de `origin/main` dans un dépôt temporaire sous la cible, SHA
  figé, export sans liens et installation du Core et de ses dépendances
  Python dans un venv local. Les versions effectivement résolues sont dans
  `requirements-installed.txt` ; elles ne constituent pas une wheelhouse
  permettant une réinstallation future hors ligne. Aucun tag ni release.
- Les migrations du **Core figé** sont exercées sur la base copiée pendant
  l'installation. Les packages Plugins sont copiés depuis ce même snapshot,
  sans activation. Les anciens packages installés et données de sessions
  Plugins ne sont pas repris. Les dépendances système GTK restent fournies
  par l'hôte en lecture seule ; les binaires locaux supplémentaires référencés
  par un chemin sont copiés comme les autres dépendances locales, mais un
  environnement externe non relogeable peut rester indisponible.
- `manifest.json` contient SHA, inventaire/empreintes, mapping des chemins,
  sources et exclusions. `verification.json` atteste les contrôles réellement
  effectués. `install.log`, `launcher.log`, `runtime.log`, code, venv, état,
  caches, HOME, configuration, livres et outils résident sous la cible.

`verify` refuse les liens/hardlinks, permissions ouvertes, chemins runtime
extérieurs, bases corrompues, références de production restantes ou
exécutables modifiés. Les données applicatives peuvent évoluer dans la copie.
Une production modifiée par son propre fonctionnement depuis la capture fait
échouer la preuve « sources inchangées » ; ce résultat ne démontre pas que
le script a effectué cette modification. Recréer une recette si nécessaire.

## Effets externes désactivés

Le lancement utilise Bubblewrap : seuls les répertoires système nécessaires
sont montés en lecture seule, la cible est le seul montage hôte inscriptible,
`/tmp`, `/run`, `/proc` et `/dev` sont privés. L'environnement est reconstruit,
HOME/XDG/TMP pointent dans la cible ; aucun bus D-Bus hôte, socket fournisseur,
agent SSH, proxy, trousseau ou périphérique de production n'est transmis.
Le machine-id et le bus sont propres à la recette, ce qui évite de réactiver
l'instance GTK de production. Le socket d'affichage X11 local est la seule
connexion hôte explicitement exposée pour afficher la fenêtre ; comme pour
toute application X11, le serveur d'affichage doit être de confiance.

L'installation pip dispose du réseau dans **un confinement séparé**, sans
accès aux chemins de production ni environnement d'authentification hérité.
Les copies déjà préparées (configurations, données, livres, imports, HOME et
état) sont également masquées par des montages temporaires pendant cette phase :
un outil d'installation ayant accès au réseau ne peut pas lire leurs credentials
ou leur contenu. Seuls code, venv et cache de dépendances persistent pour le build.
Le build local du Core et toutes les commandes suivantes sont déjà hors ligne ;
seule la récupération des dépendances binaires utilise cette phase réseau.
L'application et tous ses descendants ont ensuite un namespace réseau isolé.
Tous les comptes, hooks de téléchargement, sauvegarde automatique Kobo et LLM
sont désactivés, et le lancement impose `--no-plugins`. Les sessions/cookies
et jetons de session identifiés sont exclus de la configuration ; les caches
SQL de réponses fournisseurs sont vidés. Les mots de passe conservés dans la
copie ne sont ni imprimés ni inclus dans le manifeste. Les fichiers sont
0700/0600, et aucune donnée de production n'est publiée en CI ou sur GitHub.

Cette version permet la recette **locale et hors ligne**. Elle n'offre pas
de drapeau pour rétablir le réseau : emprunt, retour, acquisition, transfert,
Sync distante et réutilisation de sessions de production restent impossibles
via les sockets réseau de la recette. Un futur opt-in de lecture fournisseur
devra disposer d'un contrôle effectif des méthodes et de sessions neuves ;
modifier seulement un booléen de configuration ne suffirait pas.

## Recette manuelle et tests

Sur la machine possédant les données réelles, lancer d'abord `--no-start`,
consulter `verification.json`, noter le SHA, puis lancer `run.sh`. Vérifier
les identités, les fichiers locaux et les migrations sur la copie ; arrêter,
relancer puis exécuter `verify`. Arrêter et supprimer avec `destroy` ; vérifier
que la production continue normalement. Les journaux restent locaux : ils
peuvent contenir des credentials selon le contrat brut du Core et ne doivent
pas être joints automatiquement à une issue.

Les tests versionnés utilisent uniquement des fixtures synthétiques : WAL
actif, migrations réelles legacy/v14, multicomptes, chemins/JSON/URI, fichiers
externes, liens, permissions, corruption de l'inventaire, concurrence et
suppression. La CI Linux exige aussi le véritable confinement réseau/FS et
la vie du processus sous Bubblewrap (lancement, arrêt brutal, relancement,
destruction avec un processus de test). Ce test de cycle de vie n'est pas une
preuve de recette GTK sur données réelles. Sur un hôte ne permettant pas les
namespaces, ces deux tests sont explicitement ignorés localement ; la CI dédiée
pose `CIFERECA_REQUIRE_SANDBOX_TESTS=1` et interdit leur omission.
