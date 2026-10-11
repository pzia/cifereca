# GitHub Actions — budget, périmètres et déclenchement volontaire

État préparé pour revue le 11 octobre 2026. Source : `.github/workflows/`. Ce document ne chiffre **pas** les minutes effectivement facturées ; vérifier les rapports d'utilisation GitHub pour cela.

## Principe

Les modifications du Core et des plugins doivent bénéficier d'une validation déterministe, mais **la recette UX avec un environnement GTK réel est distincte de la CI courante**. Limiter les mêmes suites lancées sous plusieurs workflows à chaque PR ou push. Ne jamais déclencher une publication de release pour tester une modification structurelle.

## Matrice après nettoyage proposé

| Workflow | Déclenchement | Garde |
| --- | --- | --- |
| `pr-ci.yml` | PR modifiant le code, les tests, scripts critiques ou les workflows ; dispatch manuel | Sélection Core/Plugins/packaging/install/dataset selon fichiers changés |
| `core-tests.yml` | Push `main` concerné + dispatch | Non-régression après merge |
| `plugin-tests.yml` | Push `main` concerné + dispatch | Non-régression plugins après merge |
| `core-full-install.yml` | PR/push `main` sur périmètre déploiement + dispatch | Gate installation complète **avec rollback**, spécifique et conservé |
| `core-install.yml` | **Dispatch manuel uniquement** | Matrice Python 3.11/3.12/3.13, utile ponctuellement mais redondante à chaque push `main` |
| `e2e-smoke.yml` | **Dispatch manuel uniquement** | Recette GTK/E2E explicite et isolée, hors CI automatique |
| `gtk-smoke.yml` / `dataset-harness.yml` | Dispatch manuel | Qualification ponctuelle de runtime ou de datasets |
| `gtk-e2e-on-approval.yml` | Synchronisation PR modifiant le marqueur d'accord | **Relais temporaire Debian 13** lié à [#1011](https://github.com/pzia/cifereca/issues/1011) ; à supprimer avec `.github/gtk-e2e-run-request.txt` **après** fin de campagne |
| Releases (`release.yml`, `plugins-only-release.yml`) | Demande de publication explicite et déclencheurs documentés | Ne pas confondre tests et release |

## Gains et limites

- Éviter jusqu'à **3 jobs** (`core-install` : versions 3.11/3.12/3.13, timeout 15 min chacun) à chaque push `main` touchant le Core ; ce sont des capacités maximales, **pas des minutes consommées mesurées**.
- Éviter un job E2E GTK (timeout 10 min) sur chaque PR touchant GUI/Jeunesse/E2E. Une recette UX réelle se déclenche explicitement et garde ses artefacts.
- **Conservés :** le gate `pr-ci`, la validation Core/Plugins après merge, la validation complète avec rollback, les recettes UX à la demande et les gates de release.
- Risque à contrôler : `installed-core` et `E2E GTK · core` ne doivent pas être des checks obligatoires de protection de branche lorsque leur workflow devient manuel. **Vérifier les règles branch protection avant fusion** ; sinon ajuster ces règles ou conserver les checks existants.
- Sur `main`, la combinaison Core tests + Plugin tests + install complet reste à surveiller pour éviter un nouveau chevauchement ; décider toute réduction ultérieure sur **mesures réelles**, pas en désactivant arbitrairement les tests.
- **Frugalité de cette opération :** préparer les branches sans ouvrir de PR et sans démarrer de run tant que la vérification de coût/branch protection n'est pas faite.

## Validation hors Actions

- Contrôler la syntaxe YAML et les déclencheurs.
- Vérifier que `pr-ci` sélectionne toujours les périmètres attendus et que la recette GTK peut être déclenchée volontairement.
- Tester les scripts de sélection et le simulateur localement lorsqu'un environnement adapté est présent.
- Faire une comparaison `main...branche` avant PR et ne pas ouvrir de PR déclenchant automatiquement les gates sans accord explicite sur le budget.

Voir aussi [`environnements/README.md`](../environnements/README.md) et [`TEST-ORGANIZATION.md`](TEST-ORGANIZATION.md).
