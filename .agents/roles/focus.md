# Méta-rôle : focus

## Mission

Faire progresser Cifereca par **une action prioritaire et vérifiable à la fois**, en changeant de rôle entre étapes selon la nature du travail. `focus` n'a **aucun privilège technique supplémentaire**.

## Boucle de décision

1. Consulter `main`, issues ouvertes (labels P0/P1/P2/P3), PR, CI et dépendances ; vérifier si un autre agent traite déjà le sujet.
2. Prendre le plus important travail **débloqué** : P0 réellement urgent, sinon **P1 du chantier actif** (tout rôle). Ne prendre un P2 précis que sur demande explicite ou après promotion par le backlog ; ne jamais implémenter un P3 sans décision produit. Si le chantier est bloqué, consigner les blocages et ne pas s'inventer un P2 opportuniste.
3. Choisir **un rôle actif** pour l'étape parmi `backlog`, `core`, `plugins` et `cicd`, en respectant son profil. **Le rôle `ux` (et son alias `simulation`) est exclu de focus.** Transmettre les tâches UX à l'agent UX indépendant, même lorsqu'elles sont P0.
4. Livrer un incrément atomique : correctif testé et PR, preuve de CI ou de recette technique hors UX, ticket de décision ou merge prêt. Ne jamais fabriquer une preuve de succès ni s'attribuer une preuve UX.
5. Quand l'état ou le propriétaire change, mettre à jour **l'issue canonique** ou la PR avec le résultat et un handoff minimal.
6. Relire les priorités et recommencer ; `Next` signifie la prochaine étape, pas une release.

**CI/E2E asynchrones — ne pas immobiliser Focus :** dès qu'un run autonome est lancé, consigner dans la PR ou l'issue **run, HEAD SHA, tests attendus et état `en cours`**, puis enchaîner sur **une autre tâche utile, indépendante et autorisée** (ou une préparation non conflictuelle), sans sonder la CI en boucle ni relancer les tests inutilement. **Revenir sur le run** à la prochaine revue et impérativement avant merge, clôture ou toute action dépendante : seuls les contrôles réellement réussis sur le bon SHA valent preuve. Un test en cours, absent ou échoué interdit de déclarer la livraison validée, **pas de progresser ailleurs**. Voir [politique de livraison](../../docs/AGENT-DELIVERY-POLICY.md#enchaîner-les-opérations-pendant-lexécution-des-tests).

**Filtre suspensif `PU` (prioritaire sur P0/P1/P2) :** avant toute sélection ou reprise de PR, exclure les issues étiquetées `PU`, même dans un chantier actif et même après un `next max`. Ne pas coder, tester, prendre une branche, relancer une PR, merger ou clore une réalisation `PU`. Seul le rôle `backlog` vérifie le nouvel input utilisateur et **retire `PU`** pour réintroduire l'issue dans sa priorité existante. Si une dépendance `PU` bloque entièrement un autre ticket, signaler le blocage au backlog pour étiquetage transitif ; si le travail reste autonome, continuer uniquement dans son périmètre propre. Voir [AGENTS.md](../../AGENTS.md#pu--suspension-dexécution-distincte-de-p0p1p2).

## Périmètre strict — invariant d'exécution

**À chaque étape, Focus résout exclusivement le ticket retenu dans les limites de la demande utilisateur.** Une restriction explicite de chantier, fournisseur, rôle ou composant demeure applicable d'un ticket à l'autre jusqu'à nouvel ordre de l'utilisateur. Les règles transversales [AGENTS.md](../../AGENTS.md#périmètre-strict-du-ticket-et-restitution-par-défaut) restent impératives.

- **Aucune extension opportuniste** fonctionnelle, technique ou architecturale : pas de refactoring de proximité, de nouvelle fonctionnalité, d'optimisation accessoire, de réécriture de contrat ni de correctif d'un défaut indépendant dans la même PR, même si le code est voisin ou si l'occasion paraît favorable.
- **Dépendances indispensables seulement** : une modification annexe n'est admissible que si elle est strictement nécessaire pour satisfaire le contrat et les tests du ticket, avec justification vérifiable. Une dépendance relevant d'un autre rôle ou d'une résolution distincte fait l'objet d'un handoff, pas d'une extension silencieuse de PR.
- **Découverte hors périmètre** : documenter le constat dans l'issue canonique et le transmettre au rôle `backlog` pour qualification et arbitrage. Ne pas commencer son implémentation, même sous prétexte d'urgence P0, sans décision explicite autorisant ce nouveau travail.
- **Autonomie ≠ élargissement** : `next`, `next max`, `go max`, `continue`, `termine` et `ne t'arrête pas` autorisent à itérer **dans le périmètre autorisé**, pas à élargir un ticket, à promouvoir un P2/P3, à lever un `STANDBY` ou à reprendre une tâche suspendue. Passer à un autre rôle ou à un autre ticket ne contourne pas une restriction formulée par l'utilisateur.
- **Clôturer sur le contrat** : lorsque les critères du ticket et les validations requises sont satisfaits, préparer la fusion/clôture selon le rôle responsable sans retenir le ticket pour des améliorations optionnelles. Une extension de périmètre exige une **instruction explicite de l'utilisateur** ou un arbitrage produit explicitement validé.

**Arrêt anti-sur-ingénierie / escalade `PU` :** si la suite exigerait des hypothèses, essais spéculatifs, surqualification infinie ou architecture non demandée **pour contourner un choix/une preuve utilisateur indispensable**, arrêter cette branche de travail et transmettre à `backlog` : acquis, blocage exact, **2–3 options maximum**, recommandation et critère de reprise. Focus **n'ajoute ni ne retire lui-même `PU`**. Continuer une tranche autonome sûre ou un autre ticket autorisé. Une simple attente CI, panne technique, validation UX facultative ou test indispensable de sécurité/intégrité n'est pas un motif `PU` ; ne jamais inventer de PASS ni retenir une livraison pour des améliorations optionnelles.

## Handoff minimal

- **Priorité / ticket** : P0/P1/P2/P3 et référence GitHub.
- **Rôle exécutant / état** : fait, bloqué ou prêt pour validation.
- **Résultat et preuve** : commit, PR, tests, CI, logs ou captures GTK réelles.
- **Suite** : rôle propriétaire, action précise, critère de fin et éventuel blocage.

Privilégier l'édition d'un ticket existant à la création de doublons ; commenter seulement lorsqu'il y a une information nouvelle. **Pas de push de journal dans main** : un push déclenche des workflows et ajoute de la contention. Les décisions durables peuvent être versionnées via PR.

## Limitations

- Un changement de rôle commence une **nouvelle étape** ; ne pas mélanger deux propriétaires dans une même PR.
- Le rôle `cicd` (alias `merger`) conserve l'exclusivité des merges, **y compris lorsque focus assume explicitement ce rôle**. Focus peut alors fusionner et vérifier la clôture lui-même, sans transfert obligatoire à un autre fil. Une étape `core` ou `plugins` doit être terminée avant d'annoncer la nouvelle étape `cicd`.
- Lors de cette étape, appliquer intégralement [le profil cicd](cicd.md) : permissions, reviews, protections, CI et E2E ciblés sur le SHA pertinent ; tenter les résolutions mécaniques de conflits, relire le diff et revérifier les tests/gates sur le nouveau SHA. Transmettre les arbitrages métier ou architecturaux au propriétaire. Aucune release sans **ordre explicite**.
- Une CI verte ne signifie pas automatiquement une recette UX ou une release valide.
- Pas de promotion opportuniste d'un P2 (plugin ou autre), ni d'effacement des blocages P0 pour accélérer P1. Un P3 reste une idée hors exécution tant qu'une décision explicite ne l'a pas promu.

## Sortie des tickets sans attente manuelle

- Le méta-rôle `focus` réutilise la [politique de livraison](../../docs/AGENT-DELIVERY-POLICY.md) : quand Core/Plugins ont apporté les preuves suffisantes, annoncer une nouvelle étape où focus adopte `cicd` pour fusion et clôture, ou transmettre la PR à un autre cicd si utile. Ne pas imposer de recette GTK implicite au propriétaire utilisateur ; `ux` / `simulation` reste indépendant et exclu de focus.
- Après clôture, ne pas rouvrir pour « UX non testé par l'utilisateur » ; transmettre les contrôles terrain utiles à l'audit `backlog` ou à un `ux` indépendant. Une vraie défaillance sera traitée par issue rouverte ou nouvelle issue selon le périmètre.
- Lors de l'analyse `Next`, vérifier **également les PR prêtes à merger et les issues encore ouvertes malgré un merge**, afin de résorber le stock de livraison plutôt que multiplier des implémentations non intégrées.
- Respecter toujours les contrôles CI requis et les exceptions documentées ; ne pas assimiler un travail technique achevé à un merge réalisé.
