# Core P0 #3196 — W2 : un fichier candidat n'est pas un reçu de livraison Kobo

## Contrat du cutover

`_recover_local_remnants` trouve parfois **un seul fichier** après un
checkpoint `acquiring` ou un échec de réponse JSON/ADEPT. Avant cette
tranche, un simple nom candidat remplissait `backup_items.sha256` et
`size` en lisant les octets actuels ; le miroir W1 pouvait alors
enregistrer un Artifact de cette provenance non prouvée.

Désormais, dans ce chemin de récupération :
- la ligne historique passe toujours en `reconcile_required` et conserve
  le **chemin candidat** pour revue humaine, le bon credential, l'identité,
  le compte, l'édition et la révision antérieure ;
- `sha256` et `size` conservent **exactement** leurs anciennes valeurs,
  y compris si elles étaient absentes ;
- aucun hash des octets candidats ni taille dérivée de `stat` n'est
  enregistré, même à titre « provisoire » ;
- aucune deuxième acquisition GET/ADEPT n'est lancée ;
- les fichiers, droits, OriginLinks et références canoniques ne sont pas
  créés, déplacés ni supprimés par cette tranche.

Même un SHA réellement enregistré **avant** l'interruption ne vaut pas
preuve d'un droit fournisseur ni d'une livraison B : sa conservation est
uniquement nécessaire à une migration supervisée et à la reprise sans perte.

## Limites et réception

Cette coupure ne crée pas un nouveau mécanisme de reprise, et ne prétend
pas fermer W1/W2/W4 ou D1 : le miroir historique reste accessible sur
d'autres états. Sans SHA antérieur, W4 ne peut pas confirmer un fichier
comme une acquisition Kobo terminée. Il faut un droit observé
indépendamment, une révision exacte et un reçu de livraison compatible
Core avant toute promotion.

Régressions dans `tests/test_kobo_backup_reconcile_prune.py` et
`tests/test_core_p0_3196_w2_interrupted_no_reget.py` :
`acquiring` et réponse JSON invalide, SHA/taille absents ou préexistants,
fichier candidat physiquement préservé, hash interdit, répétition
idempotente et zéro second GET.
