# P0 #3909 — convergence post-commit sans nouveau GET

## Fenêtre de crash

Après une livraison Core validée, le code copie les octets dans
`<profil>/Acquisitions`, puis publie `Artifact+Placement+OriginLink`.
Le statut du journal `canonical-intake` et la suppression de
`.canonical-delivery/run-...` sont deux opérations **post-commit**.
Une panne de ces opérations laissait soit un reçu `canonical_receipt_staged`,
soit les octets temporaires, parfois les deux.

Dès la synchronisation suivante, la présence positive du `Placement` et
de l'`OriginLink` arrêtait la reprise sur `present_attested` sans visiter
les reçus de maintenance. Les octets pouvaient donc rester indéfiniment
en staging, et un reçu avec `artifact_id=NULL` contredire une publication
réellement terminée.

## Réconciliation limitée à un B déjà attesté

Dans le chemin `provider_satisfied`, sous lock Core d'acquisition et
consentement actuel :

1. Sélectionner uniquement le `Placement` du profil exact, présent
   physiquement dans `Acquisitions`, et les reçus Core du compte/édition
   (au plus 512, puis refus de dépassement).
2. Exiger un seul reçu canonique de livraison ; vérifier JSON sans clé
   dupliquée, `SourceKey`, offre, `_stage_id`, SHA, receipt Core
   distinct et `OriginLink` fournisseur exact. Le nom final doit être le
   nom déterministe issu du même scope, offre, reçu et SHA.
3. Vérifier les octets physiques finaux à nouveau. Si le staging existe,
   vérifier aussi ses octets et son chemin non-symlink dans le namespace
   privé. Si le dossier `run-...` a déjà disparu, ne jamais chercher un
   fichier de remplacement par SHA/titre.
4. Revalider la liaison SQLite et les gardes actives, finaliser si besoin
   le checkpoint `complete`, puis supprimer la copie privée **seulement**
   après la preuve de B et des deux fichiers.
5. Toute ambiguïté, corruption, symlink ou consentement absent conserve
   les octets. Une panne de maintenance ne dégrade jamais le B et ne
   déclenche pas de nouvel HTTP GET.

Le traitement normal de `present_attested` reste inchangé pour les
placements historiques, manuels ou déjà finalisés et nettoyés.

## Validation

Tests SQLite v14 et fournisseur synthétique : panne simulée du nettoyage
après commit, panne du statut `complete` après commit, reprise idempotente
sans GET ni Placement supplémentaire, faux compte, faux reçu, SHA falsifié,
symlink et consentement supprimé. Aucune requête fournisseur réelle.

**Limite terrain :** seuls les reçus Core avec `OriginLink` et `Placement`
finaux exacts sont éligibles. Aucune adoption de staging Numilog legacy
inconnu ni preuve que les six EPUB terrain suivent ce scénario.
