# Revue de complétude des simulateurs plugins — première itération

Commande d'inventaire (lecture seule, sans réseau) :

```sh
python3 simulation/plugin_coverage.py --json
python3 simulation/plugin_coverage.py --strict
```

La matrice `plugin_coverage.toml` doit classer **chaque package** trouvé dans
`src/plugins/*/cifereca-plugin.json`. L'inventaire sépare :
- présence des tests unitaires (nombre de fichiers, **pas résultat des tests**) ;
- service simulé réellement relié à un identifiant plugin du manifest ;
- jeux de données locaux/générés ;
- validation E2E GTK : **non attestée** par ce rapport.
Le champ `status=partial` signifie seulement qu'au moins un JDD ou serveur de\nfixtures existe,
jamais « plugin recetté ». Les écarts sont suivis dans #770 à #774.

## JDD locaux ajoutés (pilote #772 et #773)

```sh
python3 simulation/plugin_scenarios.py --out /tmp/cifereca-ux-specialized
python3 -m unittest discover -s simulation/tests -p 'test_plugin_*.py'
```

Le générateur crée des données **fictives et déterministes** :
- `youth.json` : intérêt/lecture, 0–6, 17/18, âge inconnu/inféré,
  homonymes d'éditions et preuves ISBN contradictoires ;
- `criticism.json`, `criticism.rss.xml`, `criticism.jsonfeed.json` :
  0/1/N critiques, absence d'association, sources et ISBN ;
- `constellations.json` : titres reliés ou isolés, variations d'humeur,
  séries, accès et deux identités ;
- `optimizer-valid.epub`, `optimizer-corrupt.epub` : première paire
  succès/erreur technique.

Les fichiers sont produits dans `--out`, **pas versionnés**, et n'installent
aucun abonnement, réseau, identité utilisateur ou compte externe.

**État après l'intégration :** ces fixtures sont générées dans le runtime isolé
par `datasetctl` si `[specialized].enabled = true` (profils `ux-small`,
`ux-all`, `ux-errors`) et signalées par `summary --json`.
Les flux RSS et JSON Feed de critique sont désormais **servis par HTTP local** via
`simulation/editorial/pseudo_editorial_service.py` ; les JSON Jeunesse et
Constellations sont consultables comme fixtures, mais ne sont **pas des API
plugins injectées automatiquement**. Les fichiers ne sont pas injectés dans les caches métier,
ni recettées par le harnais E2E GTK. Elles ne simulent pas les
pages GUI BookTrust/OpenLibrary (le nouveau serveur HTTP simule leurs contrats),\nla découverte distante, l'ADEPT ni les formats
PDF/DRM. Lier ces scénarios à des faux services et des tests GUI réels reste
à faire dans les tickets dédiés.

## Éditions Milan — HTTP

Le service `milan` sur 127.0.0.1:8101 sert le catalogue et les fiches HTML
synthétiques ; `MilanYouthSource` est testé via `httpx.MockTransport`
injecté, sans aucun accès réseau extérieur. Les scénarios couvrent âges
attestés ou absents, ISBN valide/invalide, 0/1/N, pagination, 404, 503,
Content-Type invalide et dépassement de taille. Aucun plugin n'est
automatiquement activé ; ce n'est **pas** un test GUI GTK.

Voir `simulation/milan/README.md` et #816.
