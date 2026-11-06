# Recommandations P1 #4681 — signaux de lecture explicitement attestés

## Tranche livrée : projection locale et désactivée par défaut

`recommendation_engine.build_profile(books)` garde **exactement** sa sémantique
existante : chaque livre visible contribue un signal documentaire neutre
(par auteur, thème, série et vocabulaire, dédupliqué à l'intérieur du livre).
Ni le statut `remote_loan_active`, ni `owned`, ni l'origine `transfer`, ni
la source fournisseur ne se convertissent en appréciation.

Le paramètre facultatif `explicit_signals`, **jamais lu dans des métadonnées
EPUB ou fournisseur**, reçoit exclusivement des `ExplicitReadingSignal`
préparés par un futur producteur Core de préférences **explicitement saisies
par l'utilisateur**. Chaque signal lie une identité exacte et une clé
`library_entries.key` exacte :

- `liked` : contribution documentaire **2** pour ce livre.
- `not_interested` : contribution documentaire **0** pour ce livre.
- Sans signal explicite : contribution **1** inchangée.

Les mentions « auteur déjà présent (N livres) » continuent de compter les **livres réels** qui participent au profil, pas la somme pondérée des affinités.

Ces coefficients constituent une **politique provisoire déterministe,
opt-in**, pour expérimentations contrôlées ; l'interface et le Core
n'injectent aucun signal aujourd'hui. Les préférences ne sont ni acquises
automatiquement ni conservées par ce module. Aucun droit, disponibilité,
possession ou autorisation d'emprunt/transfert n'est inféré. Même avec un
poids documentaire nul, les identifiants d'édition et les paires
titre/auteur restent dans l'exclusion des œuvres déjà présentes.

## Admission et anti-contamination

- Refus global de témoins mal typés, de provenance autre que
  `user_explicit`, d'identité étrangère ou de préférence contradictoire
  pour une même clé.
- Aucun jointure par titre, ISBN, provider, SHA ou compte : seule la
  **clé de bibliothèque + identité** peut appliquer un signal.
- Signal sans entrée correspondante ou entrée d'une autre identité :
  aucun poids appliqué.
- Si deux entrées **du même profil** exposent la même clé ciblée par un
  signal explicite, le calcul est refusé (`ambiguous_explicit_reading_signal_target`)
  plutôt que d'amplifier ou d'annuler arbitrairement deux livres.
  Les doublons non ciblés restent neutres pour préserver le calcul historique.
- Projections fournies par un mauvais compte : ne jamais utiliser
  cet outil comme contrôle d'accès ; `library_entries(identity_id)` doit
  être autorisé et isolé préalablement par Core.

## Tranche 2 — API de calcul opt-in

`compute_recommendations(..., explicit_signals=...)` transmet
optionnellement au moteur les témoins explicitement fournis par son
appelant. Aucun broker supplémentaire n'est interrogé : l'UI actuelle ne
fournit pas cette option. Une source étrangère ou un signal ambigu bloque le
calcul avant la consultation des catalogues.

Le cache de **bibliothèque** reste isolé par identité et peut être réutilisé :
le profil de préférence est **recalculé à chaque appel**, ce qui empêche de
réutiliser un ancien classement lorsque seuls les signaux explicites
changent. Cela ne dispense pas de prévoir l'invalidation des résultats
présentés lorsque le futur Core stockera des préférences.

## Suite de #4681

Avant activation UX, stabiliser le stockage et la preuve des préférences
dans Core (consentement, suppression, invalidation de cache, multi-profils),
puis comparer sur corpus identique les classements et la diversité
baseline vs opt-in. La notice C et les origins enrichissent les attributs
des livres, jamais leur appréciation implicite. Aucune API réseau ajoutée.
