# UX P1 #1867 — usages distincts sur l'accueil

La projection d'accueil est une **vue de provenance**, non une preuve
d'achat, d'emprunt ou de fichier présent localement.

- `numilog` et `kobo` authentifiés : affichage **Mes achats (compte)**,
  avec état de connexion. Le compteur `statistics.sources` indique
  seulement les livres **d'origine indexée**, jamais le nombre d'achats
  possédés. Le nombre de droits distants reste
  `statistics.remote_owned_rights`, indépendant du nombre de fichiers
  locaux.
- `numilog-catalog` : **Catalogue public**, sans connexion ni achat
  attesté, avec compteur d'origine uniquement. Il est ajouté aux providers
  publics déjà connus, afin qu'un catalogue public observé ne déclenche
  pas à tort une action « Connecter ».
- `paris` : source authentifiée inchangée, les prêts restent comptés
  séparément via `statistics.known_loans`.
- Le catalogue public Numilog reste une source distincte **optionnelle**.
  Kobo conserve son unique provider et son service catalogue technique :
  aucun second compte ou provider réseau n'est inventé.

Tests unitaires sur profils Numilog, Kobo, Paris et mixte ; 0/N origines
indexées, droits distants sans fichier local, et absence de faux emprunt
ou de connexion requise sur la source publique. Pas de réseau, pas de
modification du moteur de synchronisation ni de l'acquisition.

**À poursuivre dans #1867 :** libellés Bibliothèque/Recherche/Synchronisation,
regroupement graphique de sources sans fusion technique, et validation GTK
headless à 1366/1600. Ne pas clore sur cette seule tranche.
