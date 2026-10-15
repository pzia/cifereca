# Core P0 #1346 — synchronisation d'une source ≠ téléchargement Numilog

## Contrat garanti

La commande de la page **Synchronisation** utilise
`SynchronizationBrokerImpl.synchronize_source(source_id)`, qui délègue au
service Core `synchronize_account_source(account)`. Ce parcours doit rester
un **rafraîchissement des notices et des droits** d'une source explicite,
pas une acquisition locale.

Même si une session Numilog expose les capacités `personal_library` **et**
`direct_download`, son traitement :

1. Vérifie une source connectée et activée pour l'identité demandée.
2. Récupère **une fois** la liste des notices du compte.
3. Met à jour les métadonnées/catalogue et retourne des compteurs.
4. **Ne lance aucun** `_acquire_personal_library_item`, n'écrit aucun
   ebook ni ne crée de téléchargement automatique.

Ceci s'applique aux appels simples, répétés et aux synchronisations de lots
du broker. Les capacités fournisseurs et les autres providers restent
inchangés. La synchronisation détaillée (`deep`) conserve son option
de scan local déjà explicite ; elle ne déclenche pas d'acquisition Numilog.

## Transition et limite importante

Le chemin historique `LibraryService.mirror_personal_libraries` opère
toujours une acquisition Numilog qualifiée au démarrage, avec ses contrôles
existants. **Ce lot ne désactive pas cette politique historique** : sa
migration vers un déclenchement source + prévisualisation + autorisation
explicite est une décision ultérieure de l'architecture #1346/#1347.

La capacité `direct_download` est une information technique ; elle ne
constitue **jamais à elle seule** une autorisation de matérialiser toute une
bibliothèque lors d'un clic « Synchroniser la source ».

## Tests et diagnostic

`tests/test_core_p0_1346_numilog_metadata_only.py` exerce le
`LibraryService` réel et le broker public sur un Numilog factice dont
la méthode `library()` ne retourne qu'un générateur one-shot.

Le faux acquis est volontairement fatal : aucun chemin source ne doit
l'invoquer. Les tests couvrent le double appel, les deux identités, la source
désactivée et `deep`. Aucun fournisseur, téléchargement ou compte réel.

**Coordination CI/CD :** la première CI de #1759 échouait sur le callback
GLib `measured_deliver` antérieur à #1763 dans le test #981, sans rapport
avec Numilog (4 153 autres tests verts). La reprise est demandée contre
un `main` incluant le correctif P0 #1763 ; ne jamais traiter cette ancienne
exécution comme un feu vert.
