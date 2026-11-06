# Core P0 #3196 — D4: consentement on_start et compte Kobo actif exact

Le planificateur hérité `source_materialization_schedule.kobo_on_start_sessions`
peut encore déclencher l'exécuteur `backup_items` pour une session Kobo
à ABI de livraison Core incomplète. La préférence `on_start` est attachée
au **source ID de l'ancien compte**, mais le déclencheur effectif est indexé
seulement par **credential**. Après reconnexion, une session peut donc porter
le même credential avec un emprunteur/provider account différent.

Coupure D4 :

- le compte session **actuellement connecté** doit être unique, avec
  `provider_id`, `credential_id`, `unique_id` identiques aux champs de
  la source explicitement consentie. L'identité doit correspondre au
  propriétaire SQLite actuel ; seule l'ancienne valeur d'adaptateur
  `identity_id="default"` est acceptée comme alias non probant, **après**
  vérification indépendante de l'unique connexion/owner exact ;
- session sans cache de comptes, plusieurs comptes ou fournisseur changé
  => planification **manual_only**, aucun GET ;
- le parcours d'ouverture recontrôle l'identité de **la même instance**
  de session lors du dispatch et **après l'écriture du checkpoint d'essai** :
  une reconnexion entre planification et exécution est refusée sans GET ;
- les autres gardes restent inchangés : source explicitement activée,
  credential/owner non ambigu, connexion actuelle, ABI Core complète
  exclue du worker historique ;
- la préférence antérieure n'est ni effacée ni transférée au nouveau
  compte. Une nouvelle source doit consentir explicitement ; les périodes
  de retry et les checkpoints ne sont pas une permission.

Aucune création de SourceReference, droit, Artifact ou autre acquisition.
Les contrôles sont locaux (aucune nouvelle interrogation du fournisseur).
La coupure finale D4/W2 requiert encore le remplacement de l'exécuteur
0.7 pour les sources sans ABI complète.

Tests : `tests/test_core_p0_1346_explicit_on_start_schedule.py`
reconnexion de compte, identité, credential et fournisseur, comptes absents
ou multiples, blocage du parcours d'ouverture ; isolation du consentement
ancien/nouveau et réactivation du seul compte exact.
