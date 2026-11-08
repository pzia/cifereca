# P0 #4858 D1/D2 — aperçu privé Kobo sans autorité 0.7

Le manifeste historique Kobo peut être consulté par une session à ABI
incomplète et les anciennes lignes `backup_items` restent nécessaires au
diagnostic. **Aucune des deux sources n'est une preuve de livraison B.**

## Cutover de décision

- Les aperçus D1 publics **et** le motif D2 privé
  `_item_reasons` classent toute archive `complete|ok` avec chemin
  comme `needs_reconciliation`, même si SHA et taille historiques
  correspondent aux octets locaux. Aucun hash du fichier n'est nécessaire
  pour établir cette absence de provenance A→B.
- Le préflight D2 ne peut donc plus utiliser
  `already_local_linked|already_local_verified` issus du reçu 0.7
  pour valider une confirmation et un faux état « déjà présent ».
  Une archive complète dont l'identité/emprunteur et le fichier passent
  les gardes négatives est en revanche **non bloquante pour les autres
  livres** du lot : l'exécuteur W2 la met en quarantaine individuellement
  sans GET, et peut acquérir une autre édition avec consentement exact.
  Le compteur `needs_reconciliation` reste visible et le lot n'est pas
  annoncé comme B totalement satisfait.
- Un manifeste frais explicitement étranger
  (provider, credential, borrower, alias d'édition) ou marqué prêt/expiration
  suit exactement le veto W2 : `invalid_manifest_items`, aucun candidat
  `potentially_missing`, aucun GET ni écriture.
- Les lignes historiques restent lisibles et inchangées. Les vrais
  SourceReference / CompletedDelivery / OriginLink / Placement sont pris
  en charge exclusivement par l'aperçu canonique lorsque son ABI est complète.

Tests SQLite/EPUB : comptes étrangers, loan, alias, privé/public et
reçus `ok|complete` avec SHA exact ; la lecture SHA historique n'est
jamais utilisée comme preuve positive. Les confirmations du moteur
legacy restent possibles uniquement pour les éditions sans preuve
historiquement complète et admissibles sous les gardes existantes.

**Non couvert :** le vrai remplacement du premier GET W2 sans ABI complète,
et la suppression du miroir W1 à la première réception ; #4858/#3196
restent ouverts.
