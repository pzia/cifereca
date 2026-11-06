# P1 #2385 — liens de manifestations candidats sur ISBN propre

Cette tranche ajoute le planificateur **pur** `propose_same_manifestation_isbn_links`.
Elle traite uniquement les identifiants ISBN-10 et ISBN-13 **propres à la
manifestation décrite**, déjà attestés par un parseur source dont la confiance
devra être vérifiée par le futur writer Core. Le retour est volontairement
`same_manifestation_candidate`, **jamais une relation définitivement validée**.

## Conditions de rapprochement

- Identité locale exacte et namespace ISBN explicitement typé.
- Deux notices sources distinctes ; identifiants **propres** et checksum valides.
- ISBN-10 converti en ISBN-13 équivalent (préfixe 978, contrôle recalculé).
- Même ISBN canonique ET **même rôle** `print` ou `digital`.
- Champ de provenance et édition propres conservés sur chaque extrémité.
- Sources conflictuelles pour une même notice ⇒ le snapshot est refusé ; des
  alias ISBN-10/13 équivalents et preuves distinctes restent vérifiables.
- Budget borné : 512 faits et au plus 8 192 liens candidats, sans résultat
  partiel non signalé.

Sont **exclus** : ISBN d'un format associé (`own_identifier=False`),
rôle inconnu, ISBN corrompu, autre identité, titre/auteur seulement, et
l'inférence `same_work` entre un ISBN papier et un ISBN numérique.
Le planificateur ne lit ni compte ni droit/SourceReference.

## Franchissement de frontière (non livré)

Le Core doit authentifier la sémantique « ISBN propre » du parseur et le
rôle de l'édition, sa portée d'autorité, et les révisions des notices.
Seul son writer pourra créer/révoquer `NoticeRelation` à partir d'une preuve
fraîche. Un candidat n'autorise ni fusion destructive, ni héritage automatique
des métadonnées, ni consultation de données privées d'un autre profil.
Une éventuelle propagation de genre/âge exige encore le consolidateur du
type, les preuves de relation et une sortie `inherited` explicitement
référençable ; **aucun prêt, achat, offre ou DRM n'est transférable**.

La tranche est testée hors réseau et n'introduit ni table ni migration SQLite.
Elle prépare #2385 et le regroupement C de #4552, sans toucher les tables A/B.
