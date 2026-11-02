# P1 #4065 — observations d'emprunts en Paramètres (lecture seule)

Les réglages de **plafond personnel** déjà livrés par les descripteurs BnF et
Paris restent la seule source de `maximum_local` : défaut 4, saisie bornée
0–99, par identité et connexion ; pour Paris, principal et liés sont
distincts, de même que numérique/physique.

La section de compte affiche désormais un complément **non autorisant** à
partir de deux caches déjà chargés par le Core, sans réseau à l'ouverture de
Paramètres :

- `service.accounts` : seules les instances `Account` correspondant
  strictement à `provider_id + credential_id + identity_id + unique_id`
  sont admises ; le primaire Paris `unique_id=""` n'est pas un joker pour un
  compte lié.
- `service.last_loans` : seules les instances `Loan` associées à ce même
  quadruplet sont projetées, dédoublonnées par identifiant et **séparées
  numérique/physique**. Une ligne sans provenance exacte est ignorée.
- Les noms, identifiants, titres, credentials, URLs et messages d'erreur
  fournisseur ne sont jamais interpolés dans le libellé de l'observation.
- La vérification de scope réutilise le contrat typé Core. L'alias historique
  Paris `source_account_id="primary"` est admis **uniquement** pour le
  principal `unique_id=""`, jamais pour un compte lié ; si un lié porte
  exactement ce même ID et rend l'attribution ambiguë, la ligne est exclue.

**Portées et limites :**

| Notion | Source de la valeur | Interprétation |
| --- | --- | --- |
| Maximum personnel | Option utilisateur du plugin | Indicatif, jamais droit serveur |
| Prêts observés | Cache positif exact de `last_loans` | Nombre de lignes positives connues, potentiellement ancien ou incomplet |
| Inventaire exhaustif | Non attesté par le cache de GUI | Toujours non attesté ici |
| Quota restant du fournisseur | Aucune preuve depuis ce cache | Toujours inconnu |
| Emprunts restant possibles | Aucun calcul | Jamais calculé |

Si aucun prêt positif n'est présent pour une branche, la GUI indique
**« non vérifiés »**, et **jamais « 0 »** : un cache vide n'atteste pas
l'absence de prêt distant. Les comptes non connectés et les caches
malformés produisent un état indisponible, non un zéro. La valeur
personnelle n'affecte aucune décision d'emprunt, renouvellement,
synchronisation, achat, ADEPT ni publication locale.

**Limite résiduelle** : la projection d'une véritable
`LoanInventorySnapshot` avec preuve de couverture par emprunteur/branche
et du quota officiel requiert le contrat et les preuves de #4157/#3120 ;
cette tranche n'invente ni ces DTO ni de nouveaux GET. #4065 reste ouvert
pour cette intégration, sans demande de données utilisateur.
