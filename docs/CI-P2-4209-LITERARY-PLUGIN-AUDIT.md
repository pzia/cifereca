# Audit CI #4209 — package `literary-criticism` (05/11/2026)

## Objet

Réduire le nombre de **suites de packages plugins** déclenchées par une
modification produit isolée de `src/plugins/literary-criticism/**`, sans
modifier ni ignorer les tests de ce package. Les 20 autres packages
restent **non audités** et continuent à exécuter les 21 suites sur changement
de code produit. Le contrôleur `scripts/ci_select_plugin_tests.py`
reste `fail closed`.

## Inventaire vérifié sur le tree `main`

Modules de production :

| Module | Dépendances Python externes |
| --- | --- |
| `literary_criticism.py` | bibliothèque standard |
| `criticism_llm.py` | `cifereca.plugin_api.LLMRequest` |
| `criticism_feeds.py` | `cifereca.plugin_api.HTTPX_TRANSPORT`, `literary_criticism` (interne) |
| `criticism_page.py` | `cifereca.plugin_api`, `literary_criticism` et `criticism_feeds` (internes) |

Manifest : `criticism.index`, `ui.page.literary-criticism`,
`criticism.source.rss`. Pas de provider `provider.library` produit par
ce package. Aucune dépendance `requires` externe ni autre manifest
`requires_contracts` consommateur de ces catégories identifiés.

Recherche de consommateurs transverses par identifiants
`literary_criticism`, `criticism_feeds`, `criticism_page`,
`criticism_llm`, `criticism.index` et `criticism.source` :
**aucune référence trouvée dans les autres packages plugins**. Les tests
`simulation/**` et `tests/test_optional_plugins_default_disabled.py`
référencent cette fonctionnalité ; ils ne sont pas des suites d'autres
packages plugins. Leur déclenchement et leurs gates restent inchangés.

## Contrats et régression

Le test `tests/test_ci_plugin_selection.py` garantit :
1. le seul package `audited_product_packages` est `literary-criticism` ;
2. sa fermeture transitive contient uniquement ce package ;
3. une modification de `criticism_feeds.py` sélectionne sa suite ;
4. une modification produit Baen, un ensemble mixte littéraire+Baen,
   un manifest modifié, une suppression ou un renommage retombent sur
   la sélection **exhaustive** ;
5. une nouvelle importation/référence à `criticism*` dans le Python d'un
   autre package fait échouer le contrôle et impose de réauditer le graphe.

Les changements de Core/API déclenchent les règles existantes, sans
réduction du périmètre Core. Le PR CI requis, le packaging, l'installation
et les E2E restent sous leurs règles spécifiques inchangées.

## Revue et extension

Tout nouveau plugin consommateur, nouveau contrat, import dynamique,
fixture partagée ou recommandation externe doit être examiné avant
d'étendre `audited_product_packages`. Les 20 autres packages ne doivent
**pas** être ajoutés en masse sans preuve spécifique, notamment
`runtime-kobo-adept` dont les `recommends` ne sont pas couverts par
le graphe manifeste actuel.

Validation attendue : CI complète sur PR d'audit, puis test de sélection
synthétique démontrant **1/21** package visé par changement produit
littéraire et **21/21** pour un package non audité. Le gain de temps
doit être mesuré séparément ; aucun chiffre projeté n'est garanti.
