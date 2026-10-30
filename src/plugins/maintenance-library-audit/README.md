# Audits de bibliothèque (0.2.10)

Outils de maintenance disponibles : audit des couvertures, audit des langues,
contrôle d’intégrité en lecture seule, audit du nommage, réparation transactionnelle
du nommage et nettoyage des copies physiques doublonnées.

Les outils de mutation passent par le broker Core `library.maintenance` ;
le plugin ne modifie pas directement SQLite et ne télécharge pas de livres.
La suppression de doublons ne doit jamais confondre les profils ni effacer
les références de provenance.

## Retrait de l’ancien outil « Réconciliation de bibliothèque »

Depuis 0.2.10, `maintenance.audit.reconciliation` n’est plus déclaré.
Le module `cifereca_library_reconciliation.py` et ses tests de présentation
sont retirés : cette commande d’audit historique n’est pas une fonction de
synchronisation convergente et entretient un deuxième parcours pour les usagers.
Les données locales, preuves SQLite, liens d’origine, placements, acquisitions
et API internes de lecture restent inchangés. Le broker optionnel
`library.reconciliation.read` est conservé temporairement en lecture seule
pour compatibilité avec les installations et consommateurs historiques.

Le moteur canonique de synchronisation se pilote exclusivement depuis la page
Synchronisation, avec consentement ON/OFF par source et restitution des états
par fournisseur/catalogue. Une absence de preuve de présence ne doit pas bloquer
une acquisition autorisée. La déduplication intervient après réception et
placement ; un échec de maintenance ne fait pas échouer la récupération.
