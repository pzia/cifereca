# P0 #3197 — Retrait du canal mutable d'erreurs v1 Paris (30/10/2026)

Le fournisseur Paris publiait `last_loan_errors` après `loans(account)`.
Cet attribut mutable, non défini dans `ProviderSession`, n'est plus une preuve de
couverture pour le Core. Les décisions négatives reposent sur
`LoanInventorySnapshot` et ses branches DILICOM/SYRACUSE.

La version Paris 1.2.38 supprime uniquement cette publication. La méthode
`loans(account)` et ses résultats positifs restent disponibles : les deux
branches sont lues une seule fois, les refus de périmètre restent opposables,
les erreurs d'inventaire total sont signalées et les diagnostics expurgés
subsistent. Le résultat typé conserve ses codes fermés, sa protection de
l'identité et son annulation coopérative.

L'absence de `last_loan_errors` **ne prouve jamais** l'exhaustivité d'un
inventaire ni un retour de prêt ; un `loans()` vide reste partiel/non attesté.
La transition ne modifie ni acquisition, ni consentement, ni ADEPT, ni SQLite,
ni les obligations de restitution des livres empruntés.

Les tests Paris vérifient désormais directement les erreurs de transport
expurgées, les preuves typées de branches, l'isolation des emprunteurs et
l'impossibilité de consulter une propriété de statut injectée.
