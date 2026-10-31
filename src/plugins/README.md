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
- journalisation intégrale, diagnostics et health checks ; pseudonymisation **seulement du login/mot de passe dans l'IHM** ([contrat](../../docs/LOGGING-CONTRACT.md)) ;
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

Pour la synchronisation convergente #3171, consulter la
**[matrice d'observations et de preuves des quatre fournisseurs](RIGHTS-OBSERVATION-CONFORMANCE.md)** :
portées exactes, branches, couverture, limites de livraison et tests de
conformance #3252. Les faits fournisseurs ne valent ni preuve d'Artifact
ni autorisation de publication Core.

## Exemples déjà rationalisés

- Paris réutilise l'inspection et la publication publiques d'artefacts.
- Gallica conserve ses régressions OPDS/audio dans son package plugin.
- Kobo porte son contrat provider dans `src/plugins/provider-kobo/tests/`.
- Kobo-Adept est un runtime spécialisé séparé du Core.
- `transport.http` mutualise transport, health check et publication finale d'artefacts.
- **[Contrat canonique de logs](../../docs/LOGGING-CONTRACT.md)** : les plugins transmettent leurs événements et détails complets, sans suppression en amont ; seul l'affichage IHM pseudonymise login/mot de passe. Le journal/console/broker et les exports bruts demeurent intacts. Le partage de journaux bruts reste volontaire, jamais automatique.

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


## Retrait des anciens fournisseurs Jeunesse (#957)

Les plugins éditeur **Usborne** et **BookTrust** sont retirés du code livré :
leurs adaptateurs, manifests et routes de qualification ne sont plus
distribués. Les installations existantes peuvent encore contenir leurs
archives dans le magasin utilisateur, mais `PluginManager` les exclut de la
découverte et refuse toute nouvelle installation de ces packages.

**Politique de migration frugale :** aucune suppression automatique du
magasin des plugins ni du cache Jeunesse par identité. Les anciennes notices
restent consultables localement comme données historiques, avec leur
provenance d'origine et **sans réactivation de la collecte**. Si l'utilisateur
souhaite récupérer de l'espace, la suppression manuelle des anciens packages
et du cache doit être volontaire et distincte d'une mise à jour Core.
Ce retrait ne supprime pas les autres sources (Milan, École des loisirs,
Open Library).

## Retrait Jeunesse et plugins en sommeil (19/10/2026)

Les six packages spécifiques Jeunesse sont retirés et interdits de réinstallation ou de réactivation. Les caches et les versions déjà présentes dans le magasin utilisateur sont préservés, mais non découverts. Les fixtures de `simulation/` restent accessibles historiquement, sans anciens tests d'adaptateur actif. Références P3 : [catalogue #741](https://github.com/pzia/cifereca/issues/741), [Milan #946](https://github.com/pzia/cifereca/issues/946), [École des loisirs #944](https://github.com/pzia/cifereca/issues/944), [Open Library #2308](https://github.com/pzia/cifereca/issues/2308), [découverte #2309](https://github.com/pzia/cifereca/issues/2309), [enrichissement #2310](https://github.com/pzia/cifereca/issues/2310).

Le filtre d'âge **générique** en Recherche est P2 [#2311](https://github.com/pzia/cifereca/issues/2311). Le module transversal `cifereca.plugin_api.youth_ages` reste disponible, indépendant du plugin retiré.

Baen, Critique littéraire, Dev Updater et Constellations sont opt-in par `enabled_by_default=false`. Browser Recorder/Playwright est déjà opt-in.

Le plugin expérimental historique **CifericaOPDS** est retiré de la distribution (blueprint P3 [#3975](https://github.com/pzia/cifereca/issues/3975)). Une installation utilisateur ancienne n'est pas effacée automatiquement ; la distribution n'en assure plus la découverte ni les mises à jour.
