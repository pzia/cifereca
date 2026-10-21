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
