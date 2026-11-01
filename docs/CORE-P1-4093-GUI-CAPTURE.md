# P1 #4093 — Capture privée d'une session GUI (première intégration)

## Utilisation

```sh
cifereca --capture
cifereca --capture --capture-dir "$HOME/Downloads"
```

`cifereca --help` expose les deux options. `--capture-dir` sans `--capture`
est refusé. La commande sans option et `--trace` seul conservent leur
comportement : **aucun export automatique**.

Sans `--capture-dir`, le dossier de destination est fourni par
`src/cifereca/install_paths.py::downloads_dir()` (XDG user-dirs.dirs, y
compris noms localisés et espaces ; repli `~/Downloads`). La destination doit
**déjà exister**, être un répertoire et ne pas traverser de symlink. Aucun
repli silencieux vers un autre dossier si elle est inutilisable. Une erreur
`capture_directory_unavailable` est émise **avant l'ouverture Core**.

Le ZIP `cifereca-capture-YYYYMMDD-HHMMSS-<aléa>.zip` est produit
automatiquement après `app.run()`, sur fermeture normale ou interruption
`KeyboardInterrupt` traitée. Création locale atomique O_EXCL, fichier 0600,
aucun remplacement. Un chemin `capture_zip: ...` est affiché sur stderr ;
`capture_export_failed` si la publication échoue.

## Périmètre exact

- `logs/journal.log` : copie brute, **par handler de cette instance**,
  activée avant les premiers événements applicatifs et terminée après le
  message d'arrêt. Aucun scan du journal du jour ni des rotations ; les
  événements antérieurs/postérieurs et des autres instances sont exclus par
  construction. Quota de 32 Mio et 100 000 lignes, couverture `partial`
  en cas d'omission.
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
- **Reste une recette GTK + fournisseur HTTPX réel** : test d'une session
  d'interface avec recherche/emprunt/synchronisation puis fermeture.
- Aucun changement aux contrats `--trace`, Browser Recorder, diagnostics
  journaliers et `cifereca-cli sync run --trace-export`. Pas de release
  automatique.
