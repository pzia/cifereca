# Datasets UX — mode d'emploi

> **Position dans la rationalisation**  
> Ce répertoire est la simulation fonctionnelle **actuelle**. La cible future est `simulation/`, actuellement absente. Les faux services, fixtures et outils utiles seront migrés progressivement ; les éléments devenus inutiles seront supprimés. Ne pas développer en parallèle un second banc de simulation concurrent. Voir `../docs/SIMULATION.md`.

`datasetctl.py` orchestre les faux services Cifereca dans un environnement UX
entièrement isolé.

## Principes

- profils TOML versionnés ;
- ports fixes et health checks ;
- caches persistants par profil ;
- génération automatique de `config.json` et `plugins-config.json` ;
- isolation via `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME` et
  `XDG_STATE_HOME` ;
- snapshot des packages de plugins installés dans le profil UX, afin de garder
  BnF/Numilog/Home/etc. disponibles sans partager la base Cifereca réelle ;
- génération optionnelle d'une bibliothèque EPUB locale ;
- lancement Cifereca avec `--no-plugins` et les vrais `--force-plugin` du Core ;
- exécution de n'importe quel outil UX dans le même environnement ;
- capture directe via le harnais GTK/Xvfb `gtk4-environnement-tests`.

Les fichiers runtime sont stockés dans `simulation/.runtime/` par défaut.
Définissez `CIFERECA_DATASET_RUNTIME` pour les placer ailleurs.

Par défaut, `datasetctl` copie le store de plugins actif depuis le
`XDG_DATA_HOME` réel vers le profil isolé. Pour utiliser un store explicite :

```bash
export CIFERECA_DATASET_PLUGIN_SOURCE=~/.local/share/cifereca/plugins/packages
```

Cette copie n'est refaite que lorsque la signature des packages installés
change.

## Commandes

```bash
python3 simulation/datasetctl.py profiles

python3 simulation/datasetctl.py up ux-search
python3 simulation/datasetctl.py status ux-search
python3 simulation/datasetctl.py summary ux-search
python3 simulation/datasetctl.py logs ux-search
python3 simulation/datasetctl.py config ux-search

python3 simulation/datasetctl.py cifereca ux-search

python3 simulation/datasetctl.py down ux-search
python3 simulation/datasetctl.py rebuild ux-search
```

`up` est idempotent : un service déjà sain avec la même configuration est
réutilisé.

`rebuild` arrête le profil, supprime son état Cifereca isolé, ses caches de
services et sa bibliothèque générée, puis reconstruit le tout.

`summary` compare les volumes configurés aux volumes réellement générés et
aux compteurs annoncés par les health checks. `summary --json` fournit une
sortie exploitable par les scripts de recette.

## Profils livrés

| Profil | Usage |
| --- | --- |
| `ux-small` | recette rapide de tous les services |
| `ux-search` | recherche multi-source à gros volume |
| `ux-library` | bibliothèque dense : EPUB locaux + Kobo + Numilog |
| `ux-paris` | parcours Paris / PNB |
| `ux-errors` | états atypiques, assets OPDS/Kobo cassés de façon déterministe et erreurs visibles |
| `ux-all` | environnement complet |

Les profils sont dans `simulation/profiles/*.toml`.

## Lancer une commande dans l'environnement isolé

```bash
python3 simulation/datasetctl.py exec ux-library -- \
  cifereca-cli catalog-scan
```

Pour récupérer les variables XDG :

```bash
python3 simulation/datasetctl.py env ux-library
```


## Fixtures E2E reproductibles

Les scénarios E2E utilisent des déclarations versionnées dans
`simulation/e2e-fixtures/*.toml`. Chaque fixture référence un profil de
simulation dédié, déclare explicitement son état initial et utilise un nom de
runtime `e2e-*` séparé des profils UX normaux.

Commandes :

```bash
python3 simulation/e2e_fixtures.py list
python3 simulation/e2e_fixtures.py describe smoke
python3 simulation/e2e_fixtures.py prepare smoke
python3 simulation/e2e_fixtures.py reset smoke

python3 simulation/e2e_fixtures.py run empty -- \
  python3 -c 'print("scenario")'
```

`prepare` commence toujours par un reset complet de l'état isolé. `run`
garantit le nettoyage dans un `finally`, même si la commande échoue. Les
fixtures désactivent la copie du store de plugins utilisateur afin qu'un test
ne dépende jamais d'un profil réel. Le manifeste généré
`e2e-fixture.json` expose le profil, les services attendus, les chemins XDG
et l'empreinte déterministe de la bibliothèque locale.

Les fixtures livrées sont :

- `empty` : aucune donnée locale et aucun faux service ;
- `smoke` : 3 EPUB locaux, OPDS simulé et Paris simulé, avec graines fixes.

## Snapshot GTK

Le harnais GTK reste externe au dépôt. Son chemin peut être fourni par
`--harness` ou `CIFERECA_GTK_TEST_HARNESS`.

```bash
export CIFERECA_GTK_TEST_HARNESS=/chemin/gtk4-environnement-tests

python3 simulation/datasetctl.py snapshot ux-library \
  --out snapshots/library.png \
  --width 1300 --height 850 --wait 4
```

Interactions X11 optionnelles :

```bash
python3 simulation/datasetctl.py snapshot ux-search \
  --out snapshots/search.png \
  --click 408,185 \
  --type-text tour \
  --key Return
```

La capture appelle le couple `headless` / `capture-app` du harnais existant,
tout en conservant les faux services et la configuration Cifereca du profil.

## Ajouter un service

1. ajouter son script dans `simulation/<service>/` ;
2. le déclarer dans `simulation/services/catalog.toml` ;
3. ajouter les paramètres voulus dans un profil ;
4. si nécessaire, compléter la génération de configuration provider dans
   `datasetctl.py`.

Les données de livre, couvertures PNG et EPUB doivent réutiliser
`simulation/common/fake_books.py` quand leur nature est générique. Les
protocoles et états métier restent propres aux services.

OPDS et Kobo acceptent `--broken-assets-every N` pour faire échouer de façon
reproductible la couverture et le téléchargement de chaque N-ième livre. Le
profil `ux-errors` active ce mécanisme.

## Ajouter un profil

Un profil minimal :

```toml
name = "ux-mon-test"
description = "Mon scénario UX"

[cifereca]
binary = "cifereca"
no_plugins = true
force_plugins = ["ui.page.home"]
width = 1300
height = 850

[library]
epubs = 100
seed = 20261006

[services.bnf]
enabled = true
books = 20000
seed = 20261006
```

Validez-le avec :

```bash
python3 simulation/datasetctl.py doctor ux-mon-test
```

## JDD spécialisés — intégration dans datasetctl

Les profils `ux-small`, `ux-all` et `ux-errors` génèrent des fixtures
synthétiques **hors ligne** pour Jeunesse, Critique littéraire, Constellations
et Optimiseur EPUB. Elles sont déclarées par :

```toml
[specialized]
enabled = true
seed = 20261008
```

```sh
python3 simulation/datasetctl.py rebuild ux-small
python3 simulation/datasetctl.py env ux-small
python3 simulation/datasetctl.py summary ux-small --json
# données : ${CIFERECA_UX_SPECIALIZED_DATA}/youth.json et autres fichiers
```

Chaque profil possède son propre `runtime/specialized/`. Un manifeste avec
empreintes SHA-256 permet à `prepare` de conserver les fichiers inchangés,
réparer un fichier corrompu et reconstruire lors d'un changement de seed.
`rebuild` et `reset` suppriment ces fichiers avec le profil isolé.

**Attention :** leur disponibilité sur disque ne les injecte pas
automatiquement dans les caches des plugins, ne crée pas de source distante
et ne valide pas l'UI GTK. Les étapes HTTP/E2E restent suivies dans #772 et #774.

## Pseudo-service éditorial HTTP

`ux-small`, `ux-all` et `ux-errors` démarrent un service **strictement
local** sur le port 8097, sans activer automatiquement de plugin.
Les deux flux de critique (RSS, JSON Feed) sont consommables par le vrai
parseur de Critique littéraire lors d'un abonnement **explicite**.

```bash
python3 simulation/datasetctl.py up ux-small
curl http://127.0.0.1:8097/criticism/rss.xml
curl http://127.0.0.1:8097/criticism/jsonfeed.json
python3 simulation/datasetctl.py down ux-small
```

Des endpoints JSON Jeunesse/Constellations exposent les fixtures comme données
de test ; cela **n'implique aucune injection dans leurs pages GTK**.
Documentation et cas négatifs : `simulation/editorial/README.md`.

## Simulateurs jeunesse : BookTrust et OpenLibrary

Les profils `ux-small`, `ux-all` et `ux-errors` exposent désormais des
fixtures HTTP de contrats sur `127.0.0.1:8098`.

```sh
python3 simulation/datasetctl.py up ux-small
curl http://127.0.0.1:8098/health
python3 simulation/datasetctl.py down ux-small
```

Les vrais adaptateurs peuvent être testés avec un `httpx.MockTransport`
pointant explicitement sur la boucle locale (voir
`simulation/tests/test_youth_sources_contract.py`), **sans modifier**
leurs domaines autorisés. Ce mécanisme **n'injecte pas** automatiquement
les résultats dans l'écran GTK Jeunesse. Voir `simulation/youth_sources/README.md`.

## Sources jeunesse éditeurs (Usborne, école des loisirs)

Les profils UX complets exposent aussi les contrats HTML des deux éditeurs
sur `127.0.0.1:8099`. Les vrais parseurs sont vérifiés par
`simulation/tests/test_youth_publishers_contract.py` avec un transport
HTTPX local injecté ; **aucun domaine externe n'est reconfiguré**.

Ce dispositif couvre les ISBN, âges publiés (dont mois), pagination et
erreurs déterministes, **pas** l'écran GTK ni les caches réels des plugins.
Voir `simulation/youth_publishers/README.md` et #800.

## Éditions Milan — HTTP

Le service `milan` sur 127.0.0.1:8101 sert le catalogue et les fiches HTML
synthétiques ; `MilanYouthSource` est testé via `httpx.MockTransport`
injecté, sans aucun accès réseau extérieur. Les scénarios couvrent âges
attestés ou absents, ISBN valide/invalide, 0/1/N, pagination, 404, 503,
Content-Type invalide et dépassement de taille. Aucun plugin n'est
automatiquement activé ; ce n'est **pas** un test GUI GTK.

Voir `simulation/milan/README.md` et #816.

## Baen — catalogue HTML et EPUB publics fictifs

Le service `baen` est démarré sur `127.0.0.1:8100` pour les profils
`ux-small`, `ux-all` et `ux-errors`. Les tests HTTP valident le vrai
plugin Baen sur les résultats, les détails publics, le lien EPUB et les
erreurs 429/503, avec un transport **injecté** et sans réseau externe.
Le plugin n'est pas activé automatiquement : ses URLs officielles restent
inchangées. Documentation : `simulation/baen/README.md`.

Cette couverture de contrat n'est **pas une preuve E2E GTK**.

## Découverte et rapprochement Jeunesse (JDD spécialisés V2)

`datasetctl prepare` produit deux nouveaux fichiers
`youth-discovery.json` et `youth-enrichment.json` dans le runtime isolé ;
un ancien manifeste V1 est reconstruit automatiquement.

Le serveur éditorial expose respectivement
`/youth/discovery.json` et `/youth/enrichment.json`, **sans les injecter
automatiquement dans les plugins**. Les tests utilisent les vrais moteurs
de découverte et de détails avec des callback/caches mémoire injectés :
```bash
python3 -m unittest discover -s simulation/tests -p 'test_youth_workflow_contracts.py' -v
```
Le rapprochement des livres se fait par identifiants forts, jamais par
titre seul. Le moteur de découverte ne promeut jamais un candidat sans
qualification manuelle. Aucun test GTK n'est revendiqué.

## États ADEPT simulés

Le profil UX spécialisé exporte un `adept-profiles.json` fictif : aucune
identité Adobe, aucun secret et aucune liseuse réelle. Tester le véritable
adaptateur (contrat CLI JSON v8) sans installer Kobo-Adept :

```sh
python3 -m unittest discover -s simulation/tests -p 'test_adept_profile_simulation.py' -v
python3 simulation/plugin_scenarios.py --out /tmp/cifereca-ux-adept
python3 simulation/adept/fake_kobo_adept_cli.py --fixture /tmp/cifereca-ux-adept/adept-profiles.json profile list --json
```

Documentation : `simulation/adept/README.md` ; suivi : #773.

## Transport HTTP : erreurs et redirections déterministes

Le service `transport_failures` est accessible uniquement sur `127.0.0.1:8102` dans les profils `ux-small`, `ux-all`, `ux-errors`. Le vrai `HttpTransport` est testé contre 429/503, timeout, HTML ou MIME incorrect, taille excessive, réponse vide, boucle de redirection et redirection inter-origines supprimant les en-têtes sensibles. Un EPUB fictif permet aussi un téléchargement positif.

Ces tests ne constituent pas un scénario E2E GTK, une annulation de tâche ou une preuve de couverture du cache. Voir `simulation/transport/README.md` et `simulation/tests/test_http_negative_contracts.py`.

## OPDS — cas négatifs Gallica et Gutenberg

Le service OPDS existant propose des réponses d'erreur **sur demande** via
`?ux_fault=` (429, 503, XML malformé, racine XML non Atom, flux vide,
panne de page suivante, boucle de redirection) ainsi que des EPUB
invalides ou MIME HTML. `simulation/tests/test_opds_negative_contracts.py`
vérifie les **vrais** plugins Gallica/Gutenberg et `transport.http`
sur `127.0.0.1`, sans reconfigurer la production.
Voir `simulation/opds/README.md`. Pas de preuve E2E GTK.
