# P1 #1741 — corrélation opt-in du rendu GTK

La capture détaillée existante donne désormais une clé numérique **éphémère**
`operation_id` à chaque appel Bibliothèque ou Récents et la transmet aux
phases synchrones `row_projection`, `facets`, `model_update`, `selection`
et, **uniquement s'il a réellement été observé**, au premier signal GDK
`after-paint` correspondant.

Cette clé **n'est dérivée ni d'une identité, ni d'un livre, ni d'un compte,
ni d'un fournisseur, ni d'un nom de fichier**. Elle est créée dans le
`UiPerfMonitor` existant, sans nouveau journal ni réseau. Les métriques
passives et la règle opt-in demeurent inchangées ; une opération dont la frame
n'arrive pas garde ses autres mesures, sans frame artificielle à zéro.

Une génération de capture change sur une transition explicite start/stop.
Les callbacks GTK d'un ancien enregistrement sont invalides même lorsque
l'utilisateur arrête et relance la capture avant leur arrivée. Deux rendus
successifs ont des identifiants différents ; une frame tardive ne peut être
attribuée à la nouvelle capture sans avoir été réarmée par le nouveau rendu.

La mesure `frame_wait` reste la durée entre la fin du rendu synchrone et le
signal `GdkFrameClock::after-paint`, **pas** la présentation de pixels
réellement visibles, ni un diagnostic causal de non-régression. Pour la
décision #1741, il faut encore une distribution de mesures terrain
d'origine `observed`, une expérience comparable et un budget de régression.
