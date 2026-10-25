# UX P1 #1867 — source de recherche et activation de catalogues

## Portée

- **Kobo — Catalogue et compte** : une seule source sélectionnable `kobo`.
  Le catalogue Kobo est un service technique `provider.kobo.catalog` ;
  **aucune connexion secondaire** ni preuve de droit n'est créée.
- **Numilog — Mes achats (compte)** : filtre `numilog`, issu du
  fournisseur privé, pas du catalogue commercial public.
- **Numilog — Catalogue public** : filtre indépendant
  `numilog-catalog`, présent dans Recherche **seulement quand le Core
  le publie déjà comme connecté et activé pour l'identité**.
  Aucun formulaire de connexion, droit d'achat, fichier local ni
  acquisition n'est implicite.
- Les filtres de Bibliothèque peuvent afficher une provenance historique
  même lorsque sa source de recherche en ligne est désactivée ; cela ne
  l'active pas en Recherche.
- Les libellés changent uniquement l'affichage : ID de provider conservé,
  routes, sélection, capacités et données de recherche inchangées.

## Page Plugins

Les quatre rôles connus (`provider.kobo`, `provider.kobo.catalog`,
`provider.numilog` et `provider.numilog.catalog`) disposent d'une
explication fixe, non issue de texte utilisateur ou de données fournisseur.
La section **Identités…** existante reste l'unique commande d'activation
facultative du catalogue Numilog public ; elle n'est reliée ni aux
credentials du compte privé, ni au service technique Kobo.

## Vérifications

- Test unitaire de la projection et des routes, y compris source publique
  absente, ajoutée puis désactivée au changement de profil, service Kobo
  unique, sélection du compte privé préservée au retour du catalogue public.
- Banc GTK existant 1366/1600 : vraie instance `QueryControls`, source
  publique sélectionnée puis retirée, conservation de la sélection privée,
  contrôle d'allocation et capture avant retrait.
- Aucune requête réseau, lecture de credentials, activation ou téléchargement
  provoqué par le simple rendu.

La qualification réelle des recherches Numilog publiques demeure #1886 ;
la recherche Kobo via un catalogue externe reste soumise aux preuves de #1917.
Ce travail ne transforme pas un filtre de fournisseur en preuve d'achat.
