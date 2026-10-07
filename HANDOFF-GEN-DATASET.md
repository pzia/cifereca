# Handoff — simulation

> **Statut de rationalisation (7 octobre 2026)**  
> `simulation/` reste le banc de simulation fonctionnelle **actuel**. La cible documentaire est un futur répertoire `simulation/`, qui n'existe pas encore. Ce handoff décrit donc l'outil actuel et ne doit pas être interprété comme une décision de maintenir deux environnements concurrents. La migration est décrite dans `docs/SIMULATION.md`.

État de reprise au 6 octobre 2026.

## Situation

`simulation` est désormais un environnement de recette UX industrialisé, reproductible et isolé pour Cifereca.

Le dernier lot de robustesse a été mergé par la PR #9 :
- merge : `b6e570b0110b09409b6be646dd00a31b5c7c900a` ;
- CI `Dataset UX harness` verte ;
- `main` a continué à évoluer après ce merge avec les travaux Core044.

## Périmètre actuel

| Composant | Volume de référence |
| --- | ---: |
| EPUB locaux | configurable ; 1 000 dans `ux-library` / `ux-all` |
| OPDS Gallica | 10 000 |
| OPDS Gutenberg | 10 000 |
| BnF SRU | 20 000 |
| Kobo catalogue | 10 000 |
| Kobo Vault | 1 000 |
| Paris / PNB | 10 000 |
| Numilog | 1 000 |

Services enregistrés dans `simulation/services/catalog.toml` :
- `opds`
- `bnf`
- `kobo`
- `paris`
- `numilog`

Profils versionnés :
- `ux-small` : recette rapide tous services ;
- `ux-search` : gros volumes de recherche ;
- `ux-library` : bibliothèque dense ;
- `ux-paris` : parcours Paris / PNB ;
- `ux-errors` : erreurs reproductibles ;
- `ux-all` : environnement complet.

## Architecture

- `simulation/common/fake_books.py`
  - métadonnées déterministes ;
  - couvertures PNG ;
  - EPUB 3 minimal valide.
- `simulation/datasetctl.py`
  - isolation XDG ;
  - caches par profil ;
  - démarrage/arrêt/health checks ;
  - génération des configurations Cifereca ;
  - copie isolée du store de plugins ;
  - snapshots GTK via le harnais externe ;
  - résumé des volumes avec `summary`.
- `simulation/.runtime/`
  - état runtime, caches, logs, bibliothèque et configurations isolées ;
  - ignoré par Git.

Les données génériques doivent continuer à passer par `common/fake_books.py`. Les protocoles et états métier restent propres à chaque faux service.

## Robustesse déjà faite

- OPDS et Kobo utilisent maintenant les couvertures PNG communes.
- Kobo applique le filtre de langue avant pagination et retourne un total cohérent.
- Le nom du compte Kobo reflète dynamiquement la taille du Vault.
- OPDS/Kobo savent simuler des pannes d'assets déterministes avec `--broken-assets-every N`.
- `ux-errors` active ces pannes pour OPDS et Kobo.
- `datasetctl summary [--json]` compare volumes configurés, générés et reportés.
- Tests contractuels HTTP sur les cinq faux services.
- Smoke test réel `start_services -> health -> summary -> down`.
- Workflow GitHub : `.github/workflows/dataset-harness.yml`.

## Commandes de reprise

```bash
python3 simulation/datasetctl.py profiles
python3 simulation/datasetctl.py doctor ux-small
python3 simulation/datasetctl.py up ux-small
python3 simulation/datasetctl.py status ux-small
python3 simulation/datasetctl.py summary ux-small
python3 simulation/datasetctl.py down ux-small
```

Pour le gros environnement :

```bash
python3 simulation/datasetctl.py up ux-all
python3 simulation/datasetctl.py summary ux-all --json
```

Snapshot :

```bash
export CIFERECA_GTK_TEST_HARNESS=/chemin/gtk4-environnement-tests
python3 simulation/datasetctl.py snapshot ux-library \
  --out snapshots/library.png --width 1300 --height 850 --wait 4
```

## Prochaine vague recommandée

1. Étendre l'injection de pannes configurables à Paris, Numilog et BnF.
2. Ajouter des tests contractuels métier :
   - emprunt / restitution / réservation Paris ;
   - acquisition Kobo descriptor/delegated ;
   - téléchargement/redirections Numilog ;
   - diagnostics et pagination SRU BnF.
3. Valider automatiquement les artefacts générés :
   - structure EPUB ;
   - signature/dimensions PNG ;
   - forme ACSM fictive.
4. Ajouter des snapshots GTK de référence au workflow, distincts des tests fonctionnels.
5. Ajouter des profils dédiés à la charge/performance, sans mélanger ces objectifs avec les profils UX.
6. Ajouter éventuellement une commande de recette globale produisant un rapport JSON/JUnit.

## Contraintes à conserver

- génération déterministe par seed ;
- aucune dépendance réseau pour la CI ;
- faux services aussi légers que possible, bibliothèque standard Python privilégiée ;
- aucun secret ni clé DRM réelle ;
- les ACSM restent fictifs et non fulfillables ;
- isolation stricte des données/configurations Cifereca réelles ;
- ne pas dupliquer dans chaque service ce qui appartient au socle commun.

## Point de départ conseillé à la reprise

Commencer par :

```bash
python3 simulation/datasetctl.py doctor ux-small
python3 -m unittest discover -s simulation/tests -v
```

Puis traiter les points de la « Prochaine vague recommandée » par lots cohérents.
