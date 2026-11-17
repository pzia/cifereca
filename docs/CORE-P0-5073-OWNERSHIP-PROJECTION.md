# Projection des notices distantes de Recherche (#5073 / #5518)

Depuis le cutover #5975, `result_row` publie toute notice distante comme
`remote_catalog`, sans indicateur positif de possession. L’ancien registre
Entitlement n'est plus consulté par Recherche, même pour une ligne privée.
Ses anciennes preuves et flags fournisseur ne sont pas une autorisation.
Les attributs de source, compte, branche et révision demeurent disponibles.

- Catalogue public : notice descriptive, aucun achat attesté.
- Bibliothèque distante : notice descriptive, fichier local non présumé.
- Présence locale : projection de placement indépendante.
- Offre privée : hint de contrat source sélectionnée, puis vérification live
  et admission explicite Core, aucun GET au simple rendu.

Le registre historique reste lisible dans ses périmètres de compatibilité.
Le registre SourceReference v14 ne conserve pas la révision fournisseur :
une observation d'achat non révisionnée ne doit pas être présentée comme un
droit d'édition exact. L'ancien DTO SearchOwnedEvidence et son
lecteur/validateur sont retirés ; les kwargs historiques de result_row sont
acceptés mais inertes. Les chemins de Cache/Détail restent distincts.

Tests : source synthétique, preuve historique exacte et scope étranger,
flags mal formés, lecteur de droits empoisonné, offre privée explicite et
projection locale distinctes.
