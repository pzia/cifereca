# Core P0 #3196 — D1: aucun chemin backup 0.7 ne prouve une liaison locale

Le compteur `kobo_unlinked_local_count` ne consulte plus `backup_items.local_path`
pour exonérer un livre local du diagnostic « non relié ». Le statut
`complete`, `reconcile_required`, `pending_acquisition` ou `skipped_archived`
reste un **indice d'archive**, jamais une preuve de présence/liaison canonique.
Un chemin ancien identique, ou un alias via lien symbolique, ne suffit plus
à soustraire le fichier du comptage.

Deux exonérations positives subsistent, chacune avec sa preuve indépendante :

- une copie de profil explicitement matérialisée et attestée par le reçu
  local existant (sans présumer d'un droit Kobo sur le profil receveur) ;
- la publication Core B exacte : `SourceReference` active,
  `OriginLink` fournisseur et `Placement` sous l'identité, fichier actuel
  revérifié (SHA/taille/inode) et compte toujours rattaché/activé.

Un fichier physiquement présent mais connu seulement d'une archive 0.7
est donc signalé « non relié » jusqu'à preuve indépendante, sans suppression
ni déplacement de fichier. Ce compteur reste consultatif : il ne confirme
ni droit, ni acquisition, ni autorisation de GET. L'ancien mécanisme W4
de rapprochement explicite et les lectures diagnostiques des archives
restent disponibles pour la reprise ; les cutovers D1 des autres lecteurs
ne sont pas couverts par cette tranche.

Réception : `tests/test_core_p0_3196_d1_canonical_unlinked_count.py`
teste les états historiques, le compte/OriginLink, les octets physiques,
l'isolation des profils et la stabilité avant/après retrait de l'archive.
