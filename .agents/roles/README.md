# Profils de rôles agents

Le guide canonique est [AGENTS.md](../../AGENTS.md).

- `backlog` : triage, priorités, critères d'acceptation et handoffs.
- `core` : Core métier et CoreUI ; alias `coreui` pour les travaux GTK.
- `plugins` : composants fonctionnels et tests spécifiques.
- `ux` : parcours et preuves GTK ; inclut la simulation exploratoire.
- `cicd` : CI/CD, packaging, merges ; alias `merger`.
- `focus` : méta-rôle d'orchestration P0 → P1 → P2, changeant de rôle **entre** les étapes.

Un rôle actif par étape, avec un livrable vérifiable. En mode classique, le rôle reste celui choisi dans le fil ; en mode `focus`, l'orchestrateur réévalue et peut en changer à chaque étape. Aucun rôle ne franchit la frontière d'un autre pour éviter un handoff.

Lire le profil concerné avant d'agir ; les issues GitHub portent l'état, la priorité et le prochain handoff. Les PR portent le code et ses validations, jamais un journal opérationnel poussé directement dans main.
