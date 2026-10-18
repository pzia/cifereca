# Protection de `main` et contrat CI requis — #2147

## Blocage administratif

Les contrôles GitHub Actions ne sont **pas** des règles de fusion à eux seuls.
Sur `pzia/cifereca`, le constat du 18 octobre 2026 est `main.protected=false`,
sans ruleset applicable. Le rôle CI/CD peut corriger les workflows, mais la
connexion actuelle ne dispose pas des droits nécessaires à la protection de
branche. Aucun fichier dans le dépôt ne remplace ce réglage GitHub.

## Configuration administrateur

Dans **Settings → Rules → Rulesets** (ou **Branches → Branch protection**),
créer une règle applicable à `main` :

1. Exiger une pull request avant fusion ; empêcher le push direct et limiter
   les exceptions de contournement.
2. Exiger la vérification **`PR CI / PR CI`** réussie ; bloquer une PR si
   elle est encore en cours ou rouge.
3. Vérifier les conversations de revue résolues et, selon la politique d'équipe,
   la branche à jour avant fusion. Ne pas faire dépendre toutes les PR de
   workflows déclenchés seulement sur certaines familles de fichiers.
4. Tester trois cas : PR documentation seule (contexte présent et vert),
   PR rouge (merge refusé) et PR verte (merge possible).
5. Vérifier qu'aucune release Core/plugins-only n'est publiée par cette
   activation ; la publication garde son autorisation explicite, ses contrôles
   d'intégrité, signature et rollback.

## Garde-fous dans le dépôt

`.github/workflows/pr-ci.yml` conserve un déclencheur `pull_request` **sans
filtre de chemin ni de branche** et un job stable nommé `PR CI`.
La sélection interne évite les suites coûteuses lorsqu'elles sont sans rapport.
`tests/test_ci_required_check_contract.py` empêche de réintroduire un filtre
qui laisserait une PR sans contrôle obligatoire.

Les gates complémentaires (E2E GTK, installation Core, validation plugins)
restent sélectionnés selon leur périmètre. Ils ne doivent pas être inscrits
comme checks universels tant qu'ils peuvent légitimement être absents.

**Critère de fermeture #2147 :** ruleset/protection réellement active et
recette des trois cas ci-dessus ; un test de workflow vert ne suffit pas.
