# P0 #4858 W2 — Quarantaine après retour du GET opaque

L'ancien exécuteur Kobo peut avoir écrit les octets avant que Core ne
vérifie la portée de l'édition, l'emprunteur, le fichier et le SHA, ou
avant la transaction SQLite `complete`. Si une de ces opérations échouait,
le `except` générique enregistrait `status=error`, état qui pouvait
autoriser un second GET lors du manifeste suivant.

Le checkpoint `acquiring` est désormais conservé dès que l'exécuteur
opaque a **retourné**. Aucun `error` réessayable, aucun `complete`
inventé, aucun SHA, OriginLink ni Placement fabriqué depuis le résultat
invalide. Le lot indique une réconciliation requise ; le prochain passage
bloque le GET sur `acquiring`. Les erreurs de transport **avant retour**
conservent leur traitement antérieur.

Limite : un exécuteur qui effectue son GET puis lève une exception sans
retourner n'offre pas de preuve au caller ; l'ABI fournisseur canonique et
son journal de livraison sont nécessaires pour traiter cette fenêtre.
Ce jalon ne ferme ni #4858 ni #3196.
