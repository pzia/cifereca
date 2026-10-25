# Plugins P1 #2460 — un lien Web OPDS obsolète ne bloque pas l'EPUB

## Écart confirmé dans le code

Le garde-fou `EbooksSession.download_direct` vérifiait ensemble deux
informations de nature différente :

- `download_url` : **lien d'acquisition EPUB** de la notice OPDS, vérifié
  pour l'origine exacte et la route `/index.php/fetch` ;
- `friendly_url` : **lien éventuel vers une fiche HTML publique**.

Un cache issu d'anciennes versions pouvait conserver le flux Atom/XML
`/index.php/feed/...` comme `friendly_url`, tout en conservant un
`download_url` EPUB distinct parfaitement attesté. Le code rejetait alors
le téléchargement manuel avec `invalid_catalog_link`, sans même appeler
`transport.http`. C'est une cause **reproductible hors réseau** d'échec
sélectif à offre EPUB égale ; ce constat ne prétend pas diagnostiquer tous
les échecs observés sur le fournisseur réel.

## Correctif

Pour une notice `acquisition_source=opds-calibre` :

- `download_url` doit **toujours** être vérifiée strictement ; aucune URL
  inventée, aucun autre domaine, aucune route privée, aucun affaiblissement
  des redirections ni de l'inspection EPUB côté broker.
- `friendly_url` ne compte **que si** elle est validée comme lien HTML
  public. Si c'est un flux XML, un lien externe ou une route privée,
  elle est ignorée dans cette action. Elle ne bloque plus le GET EPUB
  séparé, **n'est ni passée comme Referer ni renvoyée en source_url**.
  Le Referer redevient la racine OPDS de confiance ; la valeur de retour
  redevient l'URL EPUB validée.
- Une fiche HTML attestée reste conservée comme `Referer` et comme URL
  documentaire de l'offre. Les chemins HTML hérités (simulation)
  conservent leurs garde-fous existants.
- Le Core `catalogue_notice_url` refuse déjà de présenter les
  `friendly_url` Atom/feed/fetch EbooksGratuits comme « Voir en ligne »,
  y compris depuis un ancien cache. Ce correctif complète cette frontière
  sans lancer de GET au rendu ou de récupération sur simple recherche.

Le code source du plugin est préparé en version `0.1.28`, **sans
publication de release**, ni modification de l'index des releases.

## Tests synthétiques

`src/plugins/ebooksgratuits/tests/test_opds_web_vs_acquisition_p1_2460.py` :

- EPUB exact issu d'une notice fraîche ou d'une copie de cache ;
- ancien `friendly_url` OPDS XML, feed item, URI étrangère ou lien privé
  avec paramètre sensible : aucune transmission à l'acquisition ni Referer
  extérieur, mais EPUB attesté récupérable par un clic explicite ;
- URI EPUB fausse, privée, étrangère ou échappée : refus **avant** broker ;
- fiche HTML publique correcte : continuité du Referer exact.

## Restant avant clôture #2460

Paire d'observations réelles succès/échec sur fichiers EPUB et statuses
403/404/429, extraction du contenu du broker, comparaison du cache/recherche,
et recette GTK sur Recherche/Recommandations. Les limites de débit du
fournisseur restent inchangées ; aucun téléchargement automatique.
