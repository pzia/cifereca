# Core P1 #3197 — revalidation des lectures directes de prêts legacy

`LibraryService._loans_for_account` peut encore lire les prêts positifs d'un
plugin v1 installé via `session.loans(account)`, sans snapshot typé. Le
chemin typé vérifiait déjà le bail d'époque Core et la session après I/O ;
le chemin v1 pouvait poursuivre vers la normalisation et le cache même si
le compte ou la session avait changé **pendant** cette lecture.

La tranche ajoute à la lecture historique, **avant toute écriture de cache** :

- capture avant I/O du quadruplet `identity/provider/credential/borrower`
  et de l'epoch de sélection Core ;
- contrôle après I/O que les coordonnées du même objet Account et
  l'epoch n'ont pas changé, y compris après un changement ABA réel
  matérialisé par l'epoch ;
- vérification des attributs explicites de session
  (`provider_id`, `credential_id`, `connected`) et du remplacement de la
  session si un lien de registre était présent lors de la lecture.

Un conflit lève `loan_inventory_scope_changed`. Aucun élément lu depuis
une session périmée n'est normalisé, persisté en cache ou envoyé à la
réconciliation des droits. Les anciens mocks/extensions v1 qui ne publient
pas d'attribut de connexion facultatif gardent leur comportement historique ;
la preuve de complétude d'un inventaire v1 n'est **jamais** acquise pour
autant (voir les PR d'ouverture/sync explicite de #3197).

Tests SQLite synthétiques : mutation d'identité et d'emprunteur, époque,
déconnexion, réattribution provider/credential, remplacement de session,
lecture de contrôle positive. **Aucun réseau, ADEPT, acquisition ou retour.**
Restant #3197 : transition versionnée des consommateurs de retour Paris,
contrat plugin et suppression complète des side channels historiques.
