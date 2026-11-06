# P1 #2385 / #4552 — enveloppe Core de notice source brute

## Tranche — admission d'un parsing déjà autorisé

`notice_source_observation.py` accepte une **notice métier parsée** déjà
produite par un adaptateur fournisseur/EPUB, pas un corps HTTP ni un flux
de découverte. L'enveloppe `NoticeSourceKey` conserve séparément :

- fournisseur / branche / record / édition exacte / rôle papier, numérique ou inconnu ;
- visibilité `public`, `private` (identité et credential obligatoires)
  ou `embedded` (identité et Artifact SHA exact) ;
- identité/compte propres au périmètre, sans déduire de possession ;
- parseur + version, statut `complete|partial`, horodatage d'observation.

La structure brute reçue est copiée dans un snapshot JSON canonique
**sans mapping de vocabulaire** : `Collection` n'est pas automatiquement une
`Série` ; un champ `typicalAgeRange` n'est ni une borne d'âge attestée,
ni un droit. `fields_copy()` redonne une copie indépendante. La révision
SHA opaque incorpore les coordonnées de source, la version du parseur,
le statut et les valeurs ; l'horodatage n'invalide pas seul un contenu
identique. Le résultat est inerte, non enregistré en base.

## Limites d'admission et refus fermés

Observation JSON ≤64 KiB, champ texte ≤8 KiB, nom de champ ≤128 octets,
256 champs par objet, profondeur ≤8, 4 096 nœuds, pas de NaN/objets Python
non JSON/Unicode isolé. Les clés de secrets explicites et URL signées
usuelles (signature/token/cookie/autorisation/URL avec credentials)
sont refusées, y compris profondément imbriquées, même si l'URL HTTP(S)
est en majuscules, préfixée par des espaces ou relative au protocole (`//`).
Le parseur d'origine doit
également retirer toute donnée privée non bibliographique avant émission ;
cette validation ne garantit pas seule l'absence de tout secret inconnu.

Une réponse `partial` peut enrichir un diagnostic, mais **ne constitue
jamais un inventaire complet ni une preuve d'effacement**. Aucun champ de
l'enveloppe n'autorise l'exécution d'une acquisition ou le transfert
de droits. Deux profils ou comptes distincts produisent des révisions
différentes même pour le même ISBN. Aucun lien entre éditions n'est inféré
sur le titre, l'auteur ou un ISBN associé.

## Étapes suivantes

Faire authentifier et alimenter cette enveloppe par le Core à partir
d'adaptateurs explicitement autorisés (#3724). Ajouter un writer SQLite
Core-only transactionnel et des projections C révisables, avec lecture par
portée, invalidation des anciens parseurs et conservation des bonnes
observations lors d'une page partielle. Les liens forts candidats de #4745
doivent passer une **seconde attestation**, sans promotion de possession.
Enfin, worker local de consolidation par type et registre de plugin #4552.

**Cette tranche ne modifie pas les tables A/B**, ne fait ni GET/Sync implicite,
ni migration de données anciennes, ni publication d'une Notice consolidée.
