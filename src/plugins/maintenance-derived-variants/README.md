# Maintenance des variantes dérivées

Package autonome de consultation, activé par défaut et bloqué sans le broker
`library.derived.read`. Le journal, le moteur de vérification, les quotas et
les transactions restent Core. Le plugin ne reçoit que des DTO et jetons
opaques. Mesure de capacité distincte du stock physique observé, sélection
paginée, vérification et simulation explicitement confirmée sans écriture.

Aucune reprise réelle ni compaction, aucun retrait et aucun quota libéré.
Le host guidé #7087 est nécessaire au montage. Avant raccordement, le parcours
natif reste actif ; aucune contribution double ne doit être montée.
