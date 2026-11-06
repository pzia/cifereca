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
- Projections fournies par un mauvais compte : ne jamais utiliser
  cet outil comme contrôle d'accès ; `library_entries(identity_id)` doit
  être autorisé et isolé préalablement par Core.

## Suite de #4681

Avant activation UX, stabiliser le stockage et la preuve des préférences
dans Core (consentement, suppression, invalidation de cache, multi-profils),
puis comparer sur corpus identique les classements et la diversité
baseline vs opt-in. La notice C et les origins enrichissent les attributs
des livres, jamais leur appréciation implicite. Aucune API réseau ajoutée.
