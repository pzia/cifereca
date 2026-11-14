# #5376 — inventaire des imports tests Core → modules providers

L'outil de lecture seule `scripts/ci_test_provider_import_inventory.py`
mesure des **dépendances Python attestées par AST** ; aucun test, plugin,
session, base SQLite ou fournisseur n'est exécuté.

Depuis un checkout identifié par son commit Git :

```sh
git rev-parse HEAD
python3 -m scripts.ci_test_provider_import_inventory
python3 -m scripts.ci_test_provider_import_inventory --json > /tmp/root-provider-imports.json
```

Associer au rapport le SHA du checkout : la sortie **n'embarque pas de SHA
cryptographiquement vérifié**, car l'outil est une lecture statique qui
fonctionne également sur des fixtures de test temporaires. Ne jamais
présenter un chiffre sans SHA d'origine. En cas d'erreur de lecture/parsing,
le code de sortie est non nul et les entrées concernées restent dans
`parse_or_io_errors`.

## Sens des chiffres

- `root_test_modules_scanned` : fichiers `tests/**/test_*.py` visités.
  Cela **n'est pas un nombre de cas pytest** paramétrés.
- `provider_modules_indexed` : modules Python top-level présents dans les
  répertoires `src/plugins/provider-*`, hors `__init__.py`.
- `root_test_modules_with_direct_provider_imports` : fichiers racine
  ayant au moins un import explicitement résolu.
- `direct_import_edges` : imports et symboles importés par ligne, pas
  nombre de tests exécutés ni nombre de fixtures privées.
- `private_symbol_edges` : imports dont le symbole source commence
  par `_`. Ce sont des **candidats de revue**, pas des suppressions
  automatiques ; l'underscore n'établit pas une obligation métier.
- `ambiguous_owner_edges` : même nom de module dans plusieurs plugins
  providers ; aucune attribution certaine au fournisseur.
- `unresolved_dynamic_import_calls` : appels non littéraux de loaders
  connus, **exclus des arêtes attestées**.
- `by_provider` : répartition des arêtes prouvées. Les noms de fichiers de
  tests Core ou les chaînes de caractères non importées ne sont **jamais**
  utilisés pour inférer l'appartenance fournisseur.

L'inventaire ne suit **pas** toutes les dépendances transitives : les
imports vers des fixtures situées dans d'autres fichiers racine sont
traités séparément par `ci_test_core_consumer_graph.py`. Le catalogue
des modules providers est limité aux fichiers Python top-level, pas aux
packages imbriqués ni aux imports résolus par hooks Python personnalisés.
Les imports dynamiques et collisions restent donc explicitement à
qualifier et ne sont pas présentés comme une preuve d'absence de dette.

## Critère d'action avant migration ou suppression

1. Qualifier l'arête : import de surface publique contractuelle,
   fixture privée, témoin historique ou construction de scénario.
2. Identifier **l'invariant protégé** : consentement, identité source,
   CAS, non-double GET/POST, SHA/atomicité, reprise, confidentialité.
3. Définir une fixture synthétique ou un contrat stable de remplacement
   avec le même invariant, **sans modifier les fichiers de production
   réservés au cutover #5975**.
4. Comparer l'inventaire source avant/après et conserver les preuves de
   tests réellement exécutés sur le HEAD exact ; la baisse d'arêtes AST
   seule ne vaut pas clôture.

Aucune collecte exhaustive pytest additionnelle n'est déclenchée par
ce script ou son inventaire.


## Point de mesure hebdomadaire sur `main`

Le workflow GitHub Actions
`.github/workflows/p0-test-dependency-inventory.yml` s'exécute le
dimanche à 04:17 UTC ou à la demande via `workflow_dispatch`. Après
`actions/checkout`, il vérifie que `git rev-parse HEAD` correspond au SHA
exact de l'événement GitHub sur `refs/heads/main`, puis exécute deux
analyses **AST uniquement**, sans importer les modules de tests :

- `scripts.ci_test_core_consumer_graph --json`
- `scripts.ci_test_provider_import_inventory --json`

Sur réussite complète, l'artefact
`p0-test-dependency-inventory-<SHA>` contient
`core-plugin-consumers.json`, `core-test-provider-imports.json` et
`provenance.json` (schémas, SHA exact, volumes, inconnues, zéro test
pytest réellement exécuté). Conservation : 30 jours.

La collecte échoue sans publier d'artefact si l'une des analyses présente
une erreur de parsing/lecture, si les suites ou l'index provider sont vides,
si le checkout est sale, ou si le SHA Git ne correspond pas à celui
de l'événement. Les imports dynamiques inconnus sont conservés comme
**inconnues** et ne deviennent pas artificiellement des arêtes certifiées.

Il ne s'agit ni d'une preuve de couverture métier, ni de tests réellement
exécutés, ni d'une CI exhaustive de tous les fournisseurs. Pour les
résultats d'exécution, utiliser séparément les outils `#5989/#5990`.
