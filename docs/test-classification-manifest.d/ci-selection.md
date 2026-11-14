# Lot de classification CI — sélection des suites (#5376)

Ces **8 fichiers**, vérifiés par lecture des sources sur `main` (14/11/2026),
appartiennent aux contrats de qualité de la sélection CI et non aux
implémentations métier fournisseur. Ce manifeste classe **des fichiers** :
les nombres de cas paramétrés et les exécutions réelles ne sont pas inférés.

| Fichiers | Propriété et niveau vérifiés |
|---|---|
| `test_ci_main_path_filters.py`, `test_ci_required_check_contract.py` | Contrats de routage des workflows et de statut obligatoire, assertions statiques sur fichiers de configuration |
| `test_ci_select_e2e.py`, `test_ci_gtk_smoke_privacy.py` | Contrats de sélection GTK et de confidentialité des artefacts ; ne représentent pas une exécution GTK réelle |
| `test_ci_core_main_selection.py`, `test_ci_plugin_selection.py` | Intégration des sélecteurs avec dépôt Git/fixtures synthétiques ou exécution de sous-processus |
| `test_ci_2027_e2e_replay_completeness.py`, `test_ci_pr_workflow_scopes.py` | Contrats exécutés avec shell/subprocess sur scénarios CI synthétiques, **pas** une recette de GUI réelle |

**Règle :** une ligne de manifeste atteste l'ownership du test et son niveau
principal, pas son efficacité, son taux de mutation ni sa réussite. Les
fichiers non instruits restent `unclassified`, et les manifestes chevauchants
sont refusés. Aucune suppression, aucun remplacement de tests.

Reproduire la collection réelle depuis la racine sur un HEAD connu :

```bash
python3 -m scripts.ci_test_collection_inventory --suite tests --output /tmp/cifereca-5376-collection.json
```

La collection n'exécute pas les tests ; les résultats d'exécution et les
preuves GTK doivent rester dans leurs gates respectifs.
