# Politique de livraison asynchrone, preuves et clôture des tickets

**Statut : règle commune aux agents**. Complète [AGENTS.md](../AGENTS.md) et les
profils [`.agents/roles/`](../.agents/roles/README.md). En cas de doute, les
contrats du code, les critères du ticket, les contrôles effectivement exécutés
et les protections GitHub priment sur une interprétation optimiste.

## Objectif

Plusieurs fils ChatGPT travaillent **asynchroniquement**. Le dépôt GitHub est
la seule source partagée d'avancement ; l'utilisateur n'est **pas** un agent de
recette IHM disponible en continu. **Une validation IHM manuelle de l'utilisateur
n'est jamais requise par défaut pour fusionner et fermer un ticket.**

Distinguer sans ambiguïté :

- **Implémenté** : critères techniques couverts par le code, les tests et les
  preuves propres au périmètre.
- **Livré** : PR fusionnée dans `main`, contrôles obligatoires satisfaits et
  issue fermée lorsque les critères de sortie sont remplis.
- **Vérifié sur le terrain** : observation ultérieure, facultative sauf exigence
  explicite et justifiée. Elle peut révéler un défaut et provoquer une
  réouverture ou une nouvelle issue ; elle ne rétrograde pas artificiellement
  une livraison déjà attestée.

## Douze règles exécutoires

1. **Ticket borné** : une issue canonique, un rôle pilote, des critères
   observables, les risques importants et les dépendances indispensables.
   Corriger seulement le ticket ; les anomalies indépendantes suivent leur
   propre issue.
2. **Pas de recette utilisateur implicite** : « à tester sur mon poste »,
   « en attente de validation IHM par l'utilisateur » et silence de
   l'utilisateur ne sont **pas** des raisons autonomes de conserver une PR ou
   une issue ouverte.
3. **Definition of Done** : critères d'acceptation couverts par preuves
   suffisantes, tests exigibles au vert, risques non couverts explicités,
   PR fusionnée, puis issue fermée avec liens vers PR/SHA/preuves.
   Une PR ouverte, des tests non exécutés ou une CI verte sur un ancien SHA
   ne sont pas une livraison terminée.
4. **Preuves proportionnées au risque**, suivant la matrice ci-dessous.
   Les tests unitaires et d'intégration sont le socle ; l'E2E GTK est ciblé
   lorsque le comportement visuel/interactif est l'objet du ticket ou que le
   risque l'exige.
5. **E2E en CI/CD** : `cicd/merger` possède les runners, sélecteurs, gates,
   artefacts et l'exécution des scénarios d'acceptation déjà intégrés.
   `core` et `plugins` créent/entretiennent les assertions propres à leurs
   changements et transmettent le routage nécessaire à `cicd`. Réutiliser
   `E2E Smoke`, `e2e/run.py` et les fixtures, sans nouveau workflow par
   parcours.
6. **UX autonome et tickets soumis à validation** : `ux` conduit des
   campagnes exploratoires et recettes ciblées sur demande, fournit des
   preuves, puis **propose une courte liste numérotée** (impact, priorité
   proposée, rôle) avant de créer des tickets. Seuls les items explicitement
   approuvés par l'utilisateur deviennent de nouvelles issues ; le silence
   ne vaut pas accord, l'acceptation peut être partielle. Un ticket précis
   demandé directement par l'utilisateur est déjà autorisé. `ux` ne
   corrige pas le produit, ne merge pas et ne transforme pas une observation
   en gate CI. **Cette approbation de tickets n'est pas un gate de merge** :
   l'absence de campagne exploratoire ne bloque pas la clôture.
7. **Ownership asynchrone explicite** : avant de travailler, partir de `main`,
   des issues actives et des PR ouvertes (**fichiers modifiés, HEAD SHA, checks**).
   Ne consulter les branches hors PR que si une issue, un handoff ou un indice
   concret de concurrence y renvoie ; aucun inventaire exhaustif par défaut. Un seul pilote
   de la même résolution, même entre agents d'un même rôle, avec handoff
   et dépendances visibles. Selon la [matrice des propriétaires](../AGENTS.md#coordination-anti-concurrence--un-propriétaire-par-résolution-889),
   séparer harnais CI, scénarios exploratoires et assertions fonctionnelles ;
   traverser une frontière par PR/étapes indépendantes, pas par correctif
   concurrent. Un partage de chemins n'est pas une preuve de conflit.
   Pas de verrou permanent ni de « réservation » tacite d'une issue.
8. **Déblocage, merge et clôture pilotés par `cicd`** : revue du périmètre,
   des preuves, des checks sur le SHA pertinent et des protections ;
   **tentative active de résolution des conflits Git** lorsque possible,
   avec rebase natif si approprié, contrôle du diff et relance des tests
   pertinents. Escalade au propriétaire uniquement si arbitrage fonctionnel
   ou architectural nécessaire. Fusion des PR prêtes **sans attendre une
   réponse utilisateur**, puis vérification de la fermeture du ticket.
   Aucun gate réellement échoué n'est ignoré et aucune release n'est implicite.
9. **Validation utilisateur différable** : signaler dans le ticket clos,
   sans faux « PASS », les aspects non vérifiés sur poste, matériel,
   fournisseur réel ou lecteur d'écran. Créer un suivi distinct seulement
   s'il existe un besoin de qualification concret, priorisé, avec critères.
10. **Audit backlog post-livraison** : `backlog` repère périodiquement les
    tickets récemment fermés à risque, propose les vérifications terrain
    les plus utiles et distingue « fermé, preuves CI » de « observé en vrai ».
    Une vérification absente ne rouvre pas automatiquement le ticket.
11. **Rouvrir de manière fondée** : défaut avéré du **même contrat livré**
    → réouverture de l'issue ; nouveau besoin ou défaut indépendant →
    nouvelle issue liée. Inclure reproduction, impact et preuve (logs
    expurgés, capture ou scénario). Ne pas rouvrir par simple prudence.
12. **Sécurité et intégrité non négociables** : pour les risques de perte de
    données, opérations destructives, secrets, autorisations, migrations,
    provenance ou packaging, les contrôles nécessaires doivent être
    réellement passés. Si une preuve indispensable n'est pas possible,
    documenter un blocage **réel** et demander arbitrage ; jamais inventer
    une validation ni contourner les protections de branche.

## Enchaîner les opérations pendant l'exécution des tests

**Ne pas attendre une exécution de tests : passer à l'opération suivante**, **à condition qu'elle ne dépende pas de leur résultat**. Après lancement asynchrone de tests/CI/E2E, garder sur l'issue ou la PR le lien vers le run, les suites demandées, le **HEAD SHA** et l'état honnête `en cours / non vérifié`. Prendre un autre travail utile et autorisé, sans attente active, polling répété ni nouvelle CI de relance superflue. Recontrôler les résultats lors de la prochaine revue ou avant toute opération dépendante.

**Barrière inchangée** : avant de fusionner, fermer une issue livrée, publier ou engager une action sensible, vérifier les résultats réellement exigés sur le bon SHA. Un run en cours, une absence de verdict, un job ignoré alors que requis ou un échec **ne vaut pas réussite** et impose de différer cette opération précise, **pas tout le travail de l'agent**. Respecter la politique d'exception documentée et ne jamais inventer une exécution de tests.

## Politique de preuves avant fusion

| Nature du changement | Preuves normalement attendues |
| --- | --- |
| Core/API, calcul, stockage sans IHM | Unitaires ciblés, contrats/intégration et régressions pertinentes. |
| Plugin fournisseur | Unitaires et intégration sur fixtures représentatives, erreurs et isolation ; accès réel uniquement si explicitement requis et autorisé. |
| CoreUI ou plugin IHM modifiant un parcours | Unitaires/contrat + **E2E GTK/XTEST ciblé** sur l'interaction affectée, avec assertions et artefacts pertinents. Réutiliser les scénarios existants ; compléter le scénario si indispensable. |
| Destruction, droits, données privées, transactions ou installation | Tests de sécurité, d'échec/reprise, non-régression et intégration sur la frontière touchée ; E2E ciblé s'il y a une confirmation ou une garde IHM. |
| Documentation et gouvernance seules | Relecture de cohérence, liens/rôles/contradictions et diff ; pas de campagne GTK ou de suite lourde ajoutée artificiellement. |

**Limites de preuve** : un import GTK, une capture isolée ou un test de widget
ne prouve pas un parcours complet ; un test sur simulateur ne prouve pas le
comportement d'un fournisseur externe réel. Annoncer exactement le périmètre
validé, sans exiger pour autant des tests sans rapport avec le ticket.

**E2E absent ou non adapté** : le propriétaire évalue le risque. Il ajoute une
assertion ciblée si nécessaire, demande à `cicd` de la raccorder aux
sélecteurs existants, ou justifie des preuves alternatives suffisantes.
Ne jamais substituer une validation utilisateur future à une preuve
indispensable de sécurité/intégrité. Ne jamais transformer une recette
exploratoire facultative en obligation rétroactive.

**CI indisponible ≠ tests rouges** : respecter les règles d'exception de
[`cicd.md`](../.agents/roles/cicd.md) et les protections GitHub. Un
workflow filtré mais marqué requis ne vaut pas succès ; garantir le bon
routage/sentinelle dans une issue CI distincte si nécessaire.

## Cycle d'une issue et handoffs

```text
backlog : triage / critères / rôle pilote
       ↓
core ou plugins : correction bornée + preuves + PR
       ↓
cicd : vérification SHA / gates / E2E ciblés / merge
       ↓
cicd : clôture issue avec lien PR et preuves
       ↓
backlog : audit différé des tickets clos à risque
       ↘
         ux : campagne exploratoire ciblée, si demandée
                  ↓ défaut avéré
             réouverture ou nouvelle issue
```

Chaque changement de main/issue/PR/CI est vérifié à chaque reprise de fil.
Le handoff minimal sur l'issue canonique ou la PR indique :
**numéro, rôle pilote, état (en cours/prêt pour merge/bloqué/livré), PR et
HEAD SHA, contrôles réellement passés ou manquants, blocage concret,
prochain rôle/action**. Utiliser les informations GitHub existantes plutôt
que des commentaires répétitifs ; ne pas committer un journal sur `main`.
Ne pas fermer un ticket à la seule ouverture de la PR, ni laisser une issue
livrée ouverte pour une recette exploratoire différée.

## Recette utilisateur exceptionnelle

Une recette utilisateur **préalable** n'est exigible que si un critère
explicite du ticket identifie un risque **non simulable et indispensable**
à l'acceptation (par exemple une action irréversible sur matériel réel).
`backlog` doit documenter l'impossibilité d'automatiser, le risque et
la décision avant d'en faire un blocage. Si le contrôle est utile mais
non indispensable, le sortir des critères bloquants et conserver son
statut « non vérifié » ; ne pas prétendre qu'il a réussi.

## Exemples

- Dialogue de dédoublonnage [#1421](https://github.com/pzia/cifereca/issues/1421) :
  un scénario GTK/XTEST sur nombreux doublons, petites fenêtres, défilement
  et boutons visibles peut fonder la clôture après fusion. Aucune
  recette utilisateur immédiate supplémentaire n'est requise.
- Configuration IA : tests de validation/transport simulé, E2E de
  Paramètres si concernés, confidentialité et état de redémarrage ;
  l'absence d'essai sur un compte distant réel n'empêche pas un contrat
  localement démontré si l'accès réel n'était pas indispensable au ticket.
- Kobo/ADEPT/Paris : les réponses simulées ne prouvent pas l'amont réel.
  Garder les limites explicites et séparer la qualification terrain
  nécessaire du correctif démontré ; ne pas lancer des actions sensibles
  pour combler artificiellement une preuve.
