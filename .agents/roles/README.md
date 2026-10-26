# Profils de rôles agents

> **Règle impérative commune Core/Plugins :** [skill journalisation](../skills/cifereca-logging/SKILL.md) et [contrat canonique](../../docs/LOGGING-CONTRACT.md). Tout journaliser tel qu'émis, aucun masquage hors IHM, uniquement login et mot de passe pseudonymisés à l'affichage. Les consignes historiques de listes blanches ne prévalent pas.


> **Doctrine transversale de confidentialité :** protéger exclusivement les **credentials d'authentification** (mots de passe, tokens, cookies de session authentifiants, clés privées/API, URL signées servant de jetons). Aucune tâche spéciale de confidentialité ni expurgation n'est requise pour les erreurs, traces, comptes, chemins, titres, identifiants, emails, captures sans secret ou autres informations métier. Ne pas créer ou prolonger un P0/P1 à cette seule fin. Voir [AGENTS.md](../../AGENTS.md#doctrine-de-confidentialité--credentials-uniquement-26102026) ; le rendu Diagnostics continue de pseudonymiser seulement login/mot de passe et les exports volontaires restent bruts.

Le guide canonique est [AGENTS.md](../../AGENTS.md). La politique de réalisation asynchrone, preuves, fusion et clôture est [docs/AGENT-DELIVERY-POLICY.md](../../docs/AGENT-DELIVERY-POLICY.md).

- `backlog` : **analyse et ticketing uniquement** (triage, priorités, critères d'acceptation, issues et handoffs). Aucun code, test exécuté, branche, PR, merge ni changement implicite de rôle.
- `core` : Core métier et CoreUI ; alias `coreui` pour les travaux GTK.
- `plugins` : composants fonctionnels et tests spécifiques.
- `ux` : parcours et preuves GTK ; inclut la simulation exploratoire.
- `cicd` : CI/CD, E2E d'acceptation ciblés, packaging, merges et clôtures ; alias `merger`.
- `focus` : méta-rôle d'orchestration P0 → P1 → P2 ; peut adopter `backlog`, `core`, `plugins` ou `cicd`, **jamais `ux` / `simulation`**, qui demeure un rôle indépendant.

Un rôle actif par étape, avec un livrable vérifiable. En mode classique, le rôle reste celui choisi dans le fil, **y compris après « next », « next max », « go » ou « termine »** ; seul un ordre explicite de l'utilisateur autorise à en sortir. En mode `focus` explicitement demandé, l'orchestrateur réévalue et peut en changer à chaque étape. Aucun rôle ne franchit la frontière d'un autre pour éviter un handoff.

« Seul cicd fusionne » inclut **focus lorsqu'il assume explicitement `cicd` / `merger`** : il peut effectuer lui-même les contrôles, la fusion et la vérification de clôture, selon [le profil cicd](cicd.md), sans transfert obligatoire à un autre fil. Après une étape Core/Plugins, annoncer une nouvelle étape cicd avant toute fusion ; transmettre à un autre cicd reste possible si utile. Les permissions, reviews, protections, gates sur le SHA pertinent et tentatives de résolution mécanique des conflits restent obligatoires ; les arbitrages métier reviennent au propriétaire. `ux` / `simulation` demeure indépendant et exclu de focus ; une release exige un ordre explicite.

Lire le profil concerné avant d'agir ; les issues GitHub portent l'état, la priorité et le prochain handoff. Les PR portent le code et ses validations, jamais un journal opérationnel poussé directement dans main.

## Règle commune à tous les rôles

Chaque étape vise **exclusivement le ticket retenu** et ses dépendances indispensables. Chercher une solution robuste (invariants, erreurs, cas limites et tests ciblés), sans refactoring opportuniste ni correction d'un défaut indépendant. Transmettre les sujets distincts à leur propriétaire, sans les embarquer dans la même PR.

La réponse de fin d'étape est **courte et directement dans le chat** : lien issue/PR, résultat, CI/preuves utiles et éventuel blocage. **Aucun rapport détaillé en fichier joint n'est produit par défaut** ; en créer un seulement sur demande explicite ou si une preuve spécifique le rend nécessaire.

La recette IHM utilisateur n'est **pas** un gate implicite : un ticket livré est fermé sur preuves suffisantes et fusion, les explorations UX/terrain complémentaires suivent après. Voir la politique canonique pour les exceptions de sécurité.
