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


## D1 complémentaire — aucun nouveau SHA à partir d'un ancien `complete`

Le synchroniseur 0.7 ne peut plus « vérifier une fois » un `backup_items.complete`
dont le SHA historique est absent/invalide ou dont la taille originale
n'est pas un entier positif. Avant de lire les octets, il place cette
acquisition en `reconcile_required`, **sans GET**, et conserve
`local_path`, `sha256`, `size`, la révision historique et le fichier
physique. Une empreinte fraîche d'un EPUB/PDF visible au même chemin
ne prouve pas qu'il s'agit de l'édition précédemment acquise ni d'un
téléchargement terminé ; elle ne doit donc jamais devenir le premier
baseline canonique ou historique.

Les reçus historiques dotés **avant synchronisation** d'un SHA-256
hexadécimal exact et d'une taille positive conservent provisoirement
la comparaison des octets (ancien chemin D1), sans qu'il s'agisse
d'une SourceReference ou d'un OriginLink. Cette tranche n'atteste
aucun nouveau droit ; les états sans preuve attendent revue et
rétablissement d'une provenance fournisseur indépendante. Le reliquat
D1/W2/W4 reste ouvert.

Tests : `tests/test_kobo_backup_integrity_baseline.py`
(SHA absent, corrompu, taille absente ou nulle ; pas de lecture
de fichier, pas de re-GET, pas de mutation d'empreinte, répétition
idempotente) et `tests/test_kobo_backup_guard.py`.
