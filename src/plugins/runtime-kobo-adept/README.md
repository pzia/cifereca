# Kobo-Adept runtime — inventaire isolé optionnel (#2565)

La capacité additive `loan.inventory.readonly` (package 1.2.32) expose
`loan_inventory_readonly(profile_reference, consented=False, control=None)`.
Elle ne lance rien sans `consented is True` et une référence explicite valide.
Le Core doit lier ce consentement au profil choisi et à l’identité active,
revalider le contexte avant/après lecture et oublier le résultat à sa révocation.
Cette capacité n’est pas encore branchée dans l’interface de réconciliation.

La lecture résout une référence unique via `profile list --json`, vérifie le
contrat et appelle exclusivement `loans list --profile NOM --json`. Aucun repli
sur un profil actif, création, association, retour, téléchargement ou fulfillment.
Les seuls appels CLI autorisés sont bornés par un délai partagé de 30 secondes,
4 Mio de stdout (64 Kio pour le contrat) et 64 Kio de stderr ; l’annulation et les
dépassements arrêtent et récoltent le processus POSIX isolé. Les autres plateformes
restent indisponibles. La projection stricte existante des prêts est réutilisée.

Le lecteur possède son état de contrat et de diagnostic, sans callback de
publication, journal, SQLite, cache applicatif ou modification du runtime partagé.
Le comportement interne du CLI externe doit respecter son contrat de lecture ;
aucune commande fournisseur réelle n’a été exécutée par les tests.
`ReadOnlyInventory` est privé (`repr` sans contenu) et sa méthode `redacted()` ne
rend que statut, cause fermée et compteurs. Une lecture indisponible n’est jamais
présentée comme zéro prêt. Titres, chemins, identifiants de prêt/profil restent
exclus des diagnostics. Un prêt suivi par le CLI n’atteste pas à lui seul un
achat, un droit fournisseur ou une association à un fichier local.


### Mesures de portée des lectures (#2845, 1.2.33)

Les diagnostics des lectures `contract`, `capabilities`, `profile list` et
`loans list` incluent une référence `read_scope_ref` opaque de 27 caractères.
Elle compare uniquement la configuration de commande et la portée demandée,
au sein d'une même instance du runtime : portée globale, profil par défaut
(non résolu), ou nom de profil explicite. Le nom, la commande et les arguments
ne sont pas publiés. La référence utilise un HMAC avec un secret aléatoire
privé de l'instance ; elle change à chaque instance/redémarrage et lorsque
la configuration de commande ou le profil change. Ce secret n'est ni journalisé
ni persisté. Si la mesure échoue, elle est omise sans empêcher la lecture.

La référence accompagne aussi un échec de lecture ; elle ne conserve aucune
réponse réussie et ne remplace pas les causes d'erreur. Aucun identifiant de
portée n'est produit pour les mutations, payloads, commandes inconnues ou
l'inventaire isolé de réconciliation qui n'émet pas de diagnostics.

Une même référence n'atteste **ni** l'identité Cifereca, **ni** le compte distant,
**ni** l'identité binaire/version réelle de l'exécutable, **ni** la fraîcheur
ou l'exhaustivité de l'inventaire. Elle n'autorise aucun cache/coalescence et
ne supprime aucun rafraîchissement explicite. Les lectures restent exécutées
à chaque appel. La classification des consommateurs non annotés reste
`unattributed` ; la matrice des onze appels terrain reste à qualifier.

## Contrat d’intégration cifereca ↔ Kobo-Adept

Références normatives courantes :

- [CIFERECA_KOBO_ADEPT_CONTRACT_1.3.md](./CIFERECA_KOBO_ADEPT_CONTRACT_1.3.md) — contrat d’intégration cifereca courant ;
- [KOBO_ADEPT_HANDOFF_0.13.6.md](./KOBO_ADEPT_HANDOFF_0.13.6.md) — handoff de la distribution Kobo-Adept 0.13.6.

Baseline : Kobo-Adept 0.13.6 est une release d’alignement documentaire/distribution, fonctionnellement inchangée par rapport à 0.13.5. Le contrat public Kobo-Adept reste `contract_version=8` et le download request reste en schema v2.

Le runtime doit négocier `contract --json` et `capabilities --json` ; aucune fonctionnalité ne doit être activée sur le seul numéro de version.
