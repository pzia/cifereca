# P1 #4093 — Capture privée d'une session GUI (première intégration)

## Utilisation

```sh
cifereca --capture
cifereca --capture --capture-dir "$HOME/Downloads"
```

`cifereca --help` expose les deux options. `--capture-dir` sans `--capture`
est refusé. La commande sans option et `--trace` seul conservent leur
comportement : **aucun export automatique**. Lors de la fermeture de
`--capture`, le niveau Python du logger `cifereca` est également restauré
(`DEBUG` uniquement pendant cette invocation), y compris en cas d'échec GUI,
pour ne pas laisser de journalisation plus verbeuse dans un processus réutilisé.

Sans `--capture-dir`, le dossier de destination est fourni par
`src/cifereca/install_paths.py::downloads_dir()` (XDG user-dirs.dirs, y
compris noms localisés et espaces ; repli `~/Downloads`). La destination doit
**déjà exister**, être un répertoire et ne pas traverser de symlink.
Un fichier de contrôle privé et exclusif est créé, écrit (un octet) puis
immédiatement supprimé **avant l'ouverture Core**, afin de détecter une
destination non inscriptible, y compris restrictions effectives et stockage
plein. Le test n'accorde aucune garantie contre un changement de droits ou
un disque plein **après** son exécution : l'export final conserve ses propres
gardes atomiques et son erreur fermée. Aucun repli silencieux vers un autre
dossier si elle est inutilisable ; erreur `capture_directory_unavailable`.

Le ZIP `cifereca-capture-YYYYMMDD-HHMMSS-<aléa>.zip` est produit
automatiquement après `app.run()`, sur fermeture normale ou interruption
`KeyboardInterrupt` traitée. Création locale atomique O_EXCL, fichier 0600,
aucun remplacement. Un chemin `capture_zip: ...` est affiché sur stderr ;
`capture_export_failed` si la publication échoue.

## Périmètre exact

- `logs/journal.jsonl` : copie brute, **par handler de cette instance**,
  activée avant les premiers événements applicatifs et terminée après le
  message d'arrêt. Aucun scan du journal du jour ni des rotations ; les
  événements antérieurs/postérieurs et des autres instances sont exclus par
  construction. Quota de 32 Mio et 100 000 lignes, couverture `partial`
  en cas d'omission. Une exception non traitée pendant le démarrage GUI est
  conservée **avec sa traceback brute** dans ce journal privé (code
  `capture_runtime_failed`, sortie non nulle) ; le texte de l'exception
  n'est jamais imprimé par le miroir console Trace du mode `--capture`.
- Si le niveau Trace est désactivé à n'importe quel moment de la session,
  même s'il est réactivé avant la fermeture, le manifeste indique
  `trace_interrupted=true` et `http_coverage=partial`. Les échanges survenus
  pendant la désactivation ne peuvent pas être reconstruits.
- `http/index.json`, `http/bodies/*.bin` : snapshot RAM des transports HTTPX
  déjà instrumentés par Trace. Limites Trace inchangées ; aucune nouvelle
  requête HTTP, aucune modification des flux.
- `html/<exchange-ref>.html` : copies UTF-8 uniquement de réponses HTML
  réellement **lues en entier**. Gzip/zlib et charsets déclarés sont décodés
  sous limite de 2 Mio. Le corps transport original reste sous `http/bodies/`.
  Les projections non disponibles sont tracées dans `http/index.json`
  (`html_unavailable` avec raisons fermées), sans fabriquer de HTML.
- `manifest.json` : format `cifereca-capture-session/1`, instants UTC/local,
  identité de build, numéro de processus, `session_ref`, code sortie,
  couvertures/omissions, SHA-256 + taille de chaque membre non-manifeste.
- `README.txt` : consignes de confidentialité et limites.

`validate_capture_session(path)` contrôle hors réseau le format, les membres
et leurs empreintes, sans extraction des données.

### Vérifier localement le ZIP sans relancer l'application

```sh
python3 -m cifereca.core.capture_session_export --validate '/chemin/vers/cifereca-capture-....zip' --json
```

La commande autonome (**classe F**) utilise uniquement le validateur Core
installé et lit le ZIP déjà présent : **aucun Core ouvert, aucun plugin,
aucune requête réseau et aucune écriture**. Sur succès, code retour `0` et
JSON fermé (`status=valid`, `coverage`, `members`, `format`). Sur erreur,
code retour `2`, `status=invalid`, `code=capture_archive_invalid` ; elle
n'affiche **jamais** le chemin, les headers, URLs, cookies, journaux, HTML,
ni les messages d'exception. Sans `--json`, verdict texte succinct.
La validation prouve la **cohérence interne** et les SHA des membres, pas
l'authenticité cryptographique d'un ZIP pouvant être reconstruit, ni la
réussite d'un emprunt ou d'une synchronisation. Ne pas communiquer le ZIP
brut en réponse au seul verdict « valid ».

**Limite de couverture :** le diagnostic documente les échanges HTTPX
instrumentés seulement, jamais les navigateurs externes ou transports
`requests`/`urllib`. Un ZIP ne prouve pas qu'une ressource a été empruntée,
téléchargée, publiée ni indexée. Une fermeture brutale (SIGKILL, panne disque)
ne garantit pas d'archive.

## Confidentialité

Le journal et les échanges sont **bruts**, susceptibles de contenir cookies,
SAML, tokens, URLs signées, HTML de compte, titres et données personnelles.
Aucune anonymisation implicite, aucun upload automatique. Le dossier
Téléchargements peut être synchronisé par une application tierce. Garder
l'archive locale et la vérifier soigneusement avant tout partage. Les tests CI
ne manipulent que des données synthétiques.

## Contrôles / périmètre de réception

- Unité : début/fin exacts du handler, isolation d'autres journaux/processus,
  logs multi-lignes, quotas, ZIP et SHA, gzip/charset, HTML partiel, symlink,
  lancement GUI simulé.
- Scénario **GTK réel hors compte fournisseur** :
  `capture-session-gtk-1366` via le harnais existant
  `e2e/run.py --scenario capture-session-gtk-1366`. Sous Xvfb,
  démarre `cifereca --capture --no-plugins`, attend une fenêtre GTK
  mappée, demande sa fermeture normale avec `Gtk.Window.close()`,
  valide hors réseau le ZIP 0600, son manifeste et le journal de
  démarrage/arrêt. Le ZIP contenant des données brutes reste exclusivement
  dans un répertoire temporaire **détruit avant la publication CI** ;
  seuls des booléens et codes d'échec fermés sont exportés.
- **Reste une recette GTK + fournisseur HTTPX authentifié réel** :
  test d'une session d'interface avec recherche/emprunt/synchronisation
  puis fermeture, avec consentement et revue de confidentialité.
- Aucun changement aux contrats `--trace`, Browser Recorder, diagnostics
  journaliers et `cifereca-cli sync run --trace-export`. Pas de release
  automatique.
