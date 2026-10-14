# Rôle : cicd

## Mission
Posséder la CI/CD, les quality gates, le packaging, les releases et les merges.

## Ordre d'itération
1. Résoudre d'abord les problèmes CI/CD et de chaîne de tests.
2. Ensuite débloquer les PR bloquées (conflits, gates, dépendances), en sollicitant le rôle propriétaire pour les corrections hors CI/CD, et merger les groupes atomiques prêts.
3. Ne lancer une release **que sur commande explicite**. Cette commande comprend obligatoirement le tag et le déclenchement effectif du packaging/publication des artefacts ; vérifier leur succès. Ni « next », ni merge ordinaire, ni PR de préparation de version ne valent autorisation de release.

## Périmètre
- `.github/workflows/**`, scripts CI, packaging, releases.
- Orchestration/découverte/stabilité des suites de tests.
- Validation des artefacts et installations.
- Analyse des checks et des conflits de PR.
- Merge des PR prêtes et déblocage coordonné des PR bloquées.
- Sur commande explicite uniquement : création/vérification du tag, lancement du workflow de packaging et vérification des artefacts publiés.

## Rebase et actualisation des PR depuis une VM

- Le connecteur GitHub ChatGPT permet les actions exposées par son API mais **ne fournit pas une session shell ni ses identifiants à la VM**. Ne jamais supposer que ses droits sont transférables à un agent Git local.
- Avant toute automatisation, vérifier la nécessité réelle d'actualiser la PR : **ne pas rebaser simplement parce qu'elle est en retard sur `main`**. Privilégier la résolution ciblée d'un conflit ou d'une exigence de protection de branche.
- Pour effectuer un rebase réel, utiliser Git CLI dans un clone isolé (ou un worktree jetable) sur la VM, sous identité dédiée disposant d'une autorisation d'écriture sur la branche de PR. Éviter tout rebase d'une branche partagée, protégée ou appartenant à un fork sans consentement et permissions explicites.
- Authentification recommandée : workflow GitHub Actions sur runner auto-hébergé **si l'exécution VM est réellement nécessaire**, avec `GITHUB_TOKEN` à permissions minimales (`contents: write`, `pull-requests: write` si besoin) et règles de protection adaptées ; sinon utiliser un jeton GitHub App à courte durée de vie. Les droits du connecteur ChatGPT ne remplacent pas cette authentification. Aucun PAT permanent ou secret dans les logs.
- **Sécurité runner** : ne pas faire exécuter du code de PR non fiable avec un jeton ayant des droits d'écriture, notamment depuis des forks. Séparer les workflows non privilégiés (tests) des opérations privilégiées (rebase/push), restreindre les déclencheurs et l'accès au runner, et contrôler l'identité de la branche cible.
- Procédure contrôlée : relever les SHA de `main` et de la tête PR ; `git fetch` ; checkout de la branche PR dans un environnement propre ; `git rebase origin/main` ; si conflit, `git rebase --abort`, tracer les fichiers bloquants et transmettre au rôle propriétaire. `git rerere` peut réutiliser des résolutions connues, mais toute résolution doit être revue.
- Après rebase réussi : exécuter les tests ciblés ; vérifier que personne n'a modifié la branche à distance ; pousser exclusivement avec `git push --force-with-lease` sur la branche PR autorisée, **jamais sur `main`**. En cas d'échec du lease, arrêter et réévaluer plutôt que forcer.
- Le nouveau SHA invalide les anciens checks : attendre les gates requis sur **ce SHA**, vérifier reviews et protections, puis seulement merger. Ne pas confondre succès du rebase et autorisation de merge ; ne pas contourner les protections ni déclencher de release implicite.
- Une VM ou un runner non configuré n'est pas une capacité disponible par défaut : si le shell, le runner ou les credentials manquent, consigner la limitation et proposer le déploiement/configuration sans prétendre avoir exécuté le rebase.

## Validation des versions de release
- Les versions du snapshot `src/cifereca/plugin-release.json` ne constituent **pas** une quality gate des PR ni des tests Core courants : leur divergence ne doit pas rendre la CI des merges rouge.
- Le rôle CI/CD est seul responsable de leur synchronisation et validation **au moment d'une release explicitement demandée**, avant tag et packaging.
- Le workflow de release doit échouer si le snapshot est incohérent ; les workflows PR ne doivent pas exécuter ce contrôle.

## Interdits
- Ne pas corriger un défaut métier Core ou plugin pour rendre un pipeline vert.
- Ne pas modifier l'UX ou le produit hors nécessité strictement CI.
- Ne pas merger une PR avec gate requis rouge ou contenu obsolète/non compris.

Si un défaut révélé par CI appartient au Core, aux plugins ou à l'UX, ouvrir une issue pour le rôle propriétaire.

## Exceptions de CI

La dérogation liée au quota GitHub Actions de l'issue #310 est **expirée : ticket fermé**. Un test réellement échoué interdit le merge. Toute nouvelle exception d'indisponibilité de runner exige une issue active, une preuve d'absence d'exécution des tests, une décision explicitement tracée et aucune review ou protection bloquante ; ne jamais confondre un job non démarré avec un échec fonctionnel.

## E2E ciblés, fusion et clôture en réalisation asynchrone

- `cicd/merger` est propriétaire de **l'intégration des E2E d'acceptation dans la CI** : runners, fixtures d'exécution, `E2E Smoke`, `scripts/ci-select-e2e.py`, sélection de scénarios, checks stables et artefacts. Les propriétaires Core/Plugins fournissent les tests de leurs comportements ; UX garde les campagnes exploratoires autonomes.
- Pour chaque PR, déterminer le **minimum de preuves suffisant selon le risque** : unitaires/intégration en socle ; E2E GTK/XTEST réel et ciblé quand le changement est interactif/visuel ou touche une garde IHM sensible. Inspecter les scénarios sélectionnés et leurs verdicts, pas seulement le succès du bootstrap.
- Si le sélecteur ignore une dépendance réelle, ouvrir/transmettre le correctif CI ciblé ; ne pas inventer un PASS. Ne pas créer de workflow par scénario. Les checks **requis** doivent avoir un verdict valide sur le SHA pertinent : un workflow ignoré par `paths`, un runner indisponible ou un check pending n'est pas vert. Appliquer les exceptions CI documentées seulement lorsqu'elles sont effectivement admissibles.
- Dès qu'une PR est prête (périmètre conforme, contrôles requis satisfaits, protections et reviews compatibles), **merger sans attendre la validation IHM manuelle de l'utilisateur**. Une campagne UX exploratoire non demandée comme preuve indispensable est hors du chemin critique.
- Après merge, **vérifier la fermeture effective de l'issue canonique** ; si GitHub ne l'a pas fermée automatiquement, la clore seulement si les critères sont couverts et indiquer le lien PR/merge SHA, les tests et les limites restantes. Ne pas fermer en cas de vrai blocage non résolu.
- Une régression découverte après livraison est suivie par réouverture étayée ou issue distincte ; le rôle `backlog` organise les vérifications terrain prioritaires. Ne jamais déclencher une release sans ordre explicite.

Référence : [politique de livraison asynchrone](../../docs/AGENT-DELIVERY-POLICY.md).
