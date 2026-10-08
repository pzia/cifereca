# Cifereca plugins

Les sources des plugins fonctionnels sont **actuellement** versionnées sous `src/plugins/<plugin>/`.

Chaque répertoire contient son `cifereca-plugin.json`, son code Python, ses tests et, lorsqu'ils existent, ses documents propres.

> Ne pas confondre ce répertoire avec `src/cifereca/plugins/`, qui contient le framework Core de chargement/gestion des plugins. La cible structurelle future des plugins fonctionnels est documentée dans `docs/ARCHITECTURE.md` ; aucun déplacement physique n'est implicite dans l'état courant.

Les ZIP/tar de livraison et les dépendances binaires tierces sont générés au build/release et ne constituent pas la source de vérité Git.

## Frontière Core / plugins

Le Core porte les primitives transversales et réutilisables :

- acquisition générique ;
- inspection et publication d'artefacts ;
- cache et single-flight ;
- diagnostics, health checks et redaction ;
- task broker, progression et annulation ;
- transport et contrats communs ;
- stockage, ledger et orchestration générique.

Un plugin porte ce qui dépend réellement de son domaine :

- logique fournisseur ;
- protocole/API fournisseur ;
- transformation propre au fournisseur ;
- runtime externe spécialisé ;
- tests spécifiques à ce fournisseur.

Le code de production d'un plugin importe les services Core via `cifereca.plugin_api` et non via les modules privés du Core.

Voir `docs/PLUGIN-BOUNDARIES.md`.

## Exemples déjà rationalisés

- Paris réutilise l'inspection et la publication publiques d'artefacts.
- Gallica conserve ses régressions OPDS/audio dans son package plugin.
- Kobo porte son contrat provider dans `plugins/provider-kobo/tests/`.
- Kobo-Adept est un runtime spécialisé séparé du Core.
- `transport.http` mutualise transport, health check et publication finale d'artefacts.
- La politique de redaction diagnostics est exposée publiquement et partagée.

## Tests

Les suites portent toutes le package Python `tests`. Dans ce monorepo, exécuter les tests plugin par plugin :

```bash
for p in src/plugins/*; do
  if [ -f "$p/cifereca-plugin.json" ] && [ -d "$p/tests" ]; then
    (cd "$p" && python -m pytest -q)
  fi
done
```

Placement :

- comportement propre à un plugin : `src/plugins/<plugin>/tests/` ;
- contrat transversal/Core/orchestration : `tests/`.

Voir `docs/TEST-ORGANIZATION.md`.

## Catalogues publics

Les évolutions validées de Baen, Ebooks libres et gratuits, Gallica, Project Gutenberg et BnF utilisent les surfaces publiques Core disponibles. Les éventuelles règles réseau, catalogue ou fournisseur spécifiques restent locales à chaque plugin.
