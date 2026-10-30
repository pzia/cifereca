# #3171 / #3416 — Consentement d’acquisition par compte-source

## Contrôle courant de la page Synchronisation

Le CoreUI consomme le protocole optionnel `SourceSyncConsentBroker`
([contrat et persistance](CORE-P0-3719-SOURCE-SYNC-CONSENT.md)). Un fournisseur
inconnu est présenté avec les mêmes contrôles que les fournisseurs déjà
installés ; aucun branchement par nom de fournisseur n’intervient dans ce
contrôle de consentement.

- Réglage déjà configuré : un commutateur ON/OFF porte sur tous les achats et
  prêts livrables de ce compte-source, y compris les ressources futures.
- Réglage non encore migré : deux choix explicites **Activer / Désactiver**.
  L’affichage ne transforme aucun ancien grant en consentement global et ne
  prétend pas que les anciennes autorisations sont déjà désactivées.
- Contrat de livraison non pris en charge : activation indisponible, mais
  désactivation possible pour arrêter les anciennes acquisitions automatiques.
- Source désactivée : impossible d’activer l’acquisition ; la révocation reste
  accessible. Les fichiers reçus restent conservés.
- État corrompu : aucun ON implicite ; une réparation nécessite un choix
  explicite et la révision opaque courante.

L’action transmet le `source_id` exact et la révision lue, sans identifiant de
livre, édition, droit ou offre. Le Core vérifie le CAS et le compte sous
transaction SQLite. Un autre profil, une page reconstruite ou une fenêtre
fermée invalide immédiatement le callback. Un conflit est affiché sans retry
ni écrasement automatique de la modification d’un autre client.

Afficher la page ne lance ni acquisition, ni inventaire fournisseur, ni scan
SHA, ni ADEPT. Le changement d’autorisation est local ; la Sync suivante
réadmet l’offre, la session, le consentement et les octets jusqu’au commit.
Une métadonnée de droit manquante ou inconnue ne remplace pas l’offre de
livraison et ne constitue pas un veto indépendant.

## Nettoyage et compatibilité

Le composant `canonical_consent_controls.py`, son import et les tests de
l’ancien parcours « Autoriser ce droit… » sont supprimés. Les primitives
Core de lecture des grants historiques restent présentes pour la
compatibilité ; elles ne sont plus exposées comme une étape de la page Sync.

Avec le broker courant, les cartes synchronisables utilisent le contrôle
source et ne rendent plus les anciens panneaux de préparation, audit,
confirmation de téléchargement, consentement fournisseur ou planning par
magasin. Une erreur de lecture du nouveau contrat n’ouvre pas un ancien
chemin d’acquisition. Les libellés de cette carte n’affirment plus que Sync
ne télécharge jamais de fichier.

Les hôtes anciens sans ce protocole conservent leur rendu de compatibilité,
à l’exception du contrôle par droit supprimé. Les APIs et données historiques
ne sont pas effacées. Le retrait complet des branches restantes, la projection
des connexions indisponibles/catalogues et le dernier bilan détaillé canonique
restent suivis dans #3416 ; la déduplication après placement relève de #3719.
Aucune clôture du maître ni recette fournisseur réelle n’est déduite de ce lot.

## Validation

`tests/test_core_p0_3416_source_sync_consent_ui.py` exerce callbacks et rendu
avec les mêmes contrats pour fictif inconnu, Kobo, Numilog, BnF et Paris : zéro
mutation au rendu, choix source explicite, OFF sans contrat de livraison,
CAS, scope périmé, réponse mal formée et absence d’appel aux anciens outils.

Les scénarios GTK/XTEST existants `mainwindow-baseline-1366` et
`mainwindow-baseline-1600` incluent le rendu réel d’une source inconnue,
l’activation initiale puis la révocation par clic, et un widget remplacé qui
ne peut plus écrire. Leurs résultats doivent être contrôlés en CI sur le SHA
final ; les tests à widgets doublés ne prouvent pas l’allocation GTK.
