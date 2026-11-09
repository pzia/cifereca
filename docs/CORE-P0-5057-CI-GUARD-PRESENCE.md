# P0 #5057 — contrat de présence du gate CI

La suite `tests/test_core_p0_5057_ci_gate_presence_contract.py` vérifie dans `.github/workflows/pr-ci.yml` :

- une unique étape `Block new Core provider coupling (P0 #5057)` ;
- son exécution sur les pull requests ;
- la récupération indépendante des SHA exacts de base et head à partir de l'événement GitHub ;
- l'invocation du scanner monotone avec `--base-ref` et `--head-ref` et `set -euo pipefail`.

Des mutations synthétiques du YAML (remplacement de commande, SHA substitution, étape renommée ou dupliquée, condition désactivée) sont rejetées par le test.

**Limite de sécurité :** une vérification autonome stockée dans le dépôt reste modifiable dans la même PR ; cette suite évite les désactivations accidentelles mais n'est pas une garantie anti-contournement par acteur autorisé. La protection forte relève des règles GitHub de checks requis et d'approbation/ownership des workflows, à attester séparément avant fermeture de #5057.
