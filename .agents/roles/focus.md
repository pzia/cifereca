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
