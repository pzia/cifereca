# P0 #5376 — exécution pytest et preuve liée au HEAD

L'inventaire `ci_test_collection_inventory.py` mesure les **tests collectés**
sans exécuter leurs fonctions. Il ne prouve ni les passes ni les skips effectifs.
L'outil complémentaire `ci_test_execution_inventory.py` est **manuel et opt-in** :
il exécute le périmètre demandé, capture les phases réelles de pytest et
réutilise les manifestes de classification explicites du dépôt.

Depuis un clone avec HEAD Git propre :

```bash
python3 -m scripts.ci_test_execution_inventory \
  --suite tests \
  --nodeid tests/test_public_httpx_transport.py::test_mixed_public_private_dns_answers_fail_closed \
  --output /tmp/cifereca-execution-5376.json
```

Le même outil accepte `--suite src/plugins/<package>/tests` pour une suite
plugin ciblée. Omettre `--nodeid` exécute toute la suite choisie ; aucune
collecte ou exécution globale supplémentaire n'est imposée à chaque PR.

## Schéma `cifereca.pytest-execution-inventory/1`

- `source_sha` : commit inchangé avant et après l'exécution ; publication
  refusée sur une copie de travail modifiée ou un SHA invalide.
- `suite` et `selection_scoped` : périmètre effectif, sans prétendre qu'un
  `--nodeid` a inventorié toute la suite.
- `count_selected` et `count_deselected` : sélection pytest.
- `count_attempted` : cas entrés dans `setup`.
- `count_executed` : cas ayant réellement atteint `call` ; un skip ou une
  erreur de fixture en `setup` n'entre pas dans ce décompte.
- `outcomes` : succès, échec d'assertion, erreur de setup/teardown,
  skip, xfail et xpass **observés**. Les marqueurs ne valent pas verdict.
- `matrix` et `items` : attribution `owner_architecture × business_object ×
  test_level × outcome`, avec `unclassified` si non attesté ; identifiants
  paramétrés conservés.
- `reported_phase_duration_seconds` : somme des durées de phases, non durée
  totale du job CI.

Les résultats ne contiennent aucun traceback, sortie capturée, URL ou secret.
Un échec pytest complet avec code 1 reste publiable comme **échec** et la
commande échoue ; une collecte incomplète, un run interrompu, une phase finale
manquante ou des doublons ne remplacent pas le dernier rapport valide. Écriture
atomique seulement après validation.

**Limite :** un même nodeid dans deux gates correspond à deux exécutions mais
à un seul cas unique. Ne pas additionner ces décomptes sans déduplication,
ni les confondre avec E2E GTK, simulation ou recette fournisseur authentifiée.

Les mutations sur les contrats consentement, identité et système de fichiers
restent un travail distinct de #5376 et n'autorisent aucune intervention sur
les fichiers de production de #5975.
