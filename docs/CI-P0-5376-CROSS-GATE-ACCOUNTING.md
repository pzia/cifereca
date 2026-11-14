# P0 #5376 — cas uniques et passages répétés entre gates

`ci_test_execution_aggregate.py` est un consolidateur **hors CI systématique**
de rapports produits par `ci_test_execution_inventory.py`. Il ne lance aucun
pytest et ne transforme pas un skip en exécution réelle.

Exemple de consolidation de deux rapports existants sur **le même commit** :

```bash
python3 -m scripts.ci_test_execution_aggregate \
  --gate core=/tmp/cifereca-core-execution.json \
  --gate plugins=/tmp/cifereca-plugins-execution.json \
  --output /tmp/cifereca-executions-consolidees.json
```

Chaque `--gate LABEL=PATH` porte une déclaration d'origine humaine ou CI.
L'outil refuse les libellés dupliqués, deux copies identiques d'un rapport,
les inventaires incomplets, les SHA différents et les comptes internes
contradictoires. Chaque entrée doit attester un `HEAD` propre.

## Indicateurs non interchangeables

| Champ | Signification |
|---|---|
| `count_unique_cases` | Nombre de nodeids distincts au même SHA |
| `count_selected_observations` | Somme des cas sélectionnés observés dans les rapports |
| `count_called_executions` | Somme des cas ayant effectivement atteint la phase pytest `call` |
| `count_replayed_cases` | Cas présents dans au moins deux gates |
| `count_extra_selected_observations` | Doublons de sélection au-delà du premier passage de chaque nodeid |
| `count_reexecuted_cases` | Cas ayant atteint `call` dans au moins deux gates |
| `count_extra_called_executions` | Appels supplémentaires au-delà du premier appel par cas |
| `count_discordant_replayed_cases` | Nodeids dont les verdicts changent entre gates |
| `matrix` | Cas uniques, observations et appels par propriété/objet/niveau |

La matrice conserve les classes `unclassified`. Les outcomes observés sont
comptés séparément et aucun statut ne remplace la vérité des tests ;
`all_gates_passed=false` dès qu'un rapport d'entrée possède `exit_code=1`.
Un échec complet produit un JSON exploitable mais la commande retourne 1.

**Limites :** deux libellés de gates ne prouvent pas, à eux seuls, deux
exécutions indépendantes ; l'outil rejette simplement les copies d'octets
identiques et s'appuie sur les provenance déclarées. Un même nodeid dans deux
environnements ne signifie pas deux contrats métier couverts. Le consolidateur
ne mesure ni les suites non-pytest, ni la couverture des permissions, droits,
reprises ou transports. Ne pas additionner des HEAD différents.

La migration des fixtures legacy et les expériences de mutation supplémentaires
relèvent des autres critères de #5376, sans intervention sur #5975.
