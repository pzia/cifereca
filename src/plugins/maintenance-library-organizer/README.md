# Organiser la bibliothèque

Package autonome, activé par défaut. Le port public `library.organizer.read`
est indispensable ; sans lui le package reste bloqué, sans fallback vers Storage.
Le moteur de revue, le nommage, la politique keeper et les preuves restent Core.

Aperçu paginé et « Tout accepter (avis) » ne produisent qu'une sélection
consultative. Aucun déplacement, création de dossier, retrait ou droit.
Le parcours guidé requiert l'extension additive `GuidedMaintenanceTool`.
Avant son raccordement host et l'enregistrement du broker, l'ancienne ligne
native reste le seul parcours actif ; le package ne crée pas de deuxième moteur.


## Réception technique du renommage isolé (24/11/2026)

Le plan confirmé ne constitue pas une autorisation de mutation.
Le pont natif de renommage unitaire délègue au writer Core
`LibraryMaintenanceBrokerImpl` ; `_execution_fence_current` reste fermé
en production. Le classement, le déplacement inter-dossiers et le retrait
de doublons ne sont pas activés par ces preuves.

| Preuve intégrée | Fusion sur main | Recette |
| --- | --- | --- |
| #8014 | `26e6fb0e4e7b0082544a94bcb11bea8ecf9e4af3` | SHA physique après commit, dérive des octets ou lien symbolique |
| #8024 | `5c7889900fe9f0de8a94f268caaf58666d8b99d6` | Stabilité SQLite/WAL et absence de l'ancien nom durant la relecture SHA |
| #8027 | `1d0bd54037e91db93594454fe22407eaff382df6` | Arrêt d'un processus avant commit, après commit et après retrait de l'ancien nom ; réouverture indépendante SQLite |

Gates exactes : #8014 PR CI 45478924486, installation 45478924462 et AST
45478924373 ; #8024 PR CI 45482193459, installation 45482193232 et AST
45482193354 ; #8027 PR CI 45484068773. Ces gates sont vertes et les commits
sont intégrés à main.

Après une interruption, l'ancien et le nouveau nom peuvent encore désigner
le même inode. La recette vérifie qu'un seul Placement fait autorité, avec
le chemin actif C correspondant et les octets SHA attendus. Ce n'est pas
un consentement à retirer l'autre nom. Un postcheck incertain rapporte
`recovery_required`, sans rollback spéculatif ni second writer.

**Reste à réceptionner :** qualification indépendante du verrou consommateur
et des références actives pour le parcours complet (rangement, keeper,
lots et reprise explicite), sans lever le gel destructif. La recette
ci-dessus concerne un renommage isolé sur fixtures, pas une migration
de bibliothèque réelle ni une preuve de nettoyage autorisé.
