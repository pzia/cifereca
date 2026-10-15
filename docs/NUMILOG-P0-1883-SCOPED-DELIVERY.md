# Numilog P0 #1883 — livraison ACSM depuis une fiche possédée

## État de qualification (15/10/2026)

**Anomalie réelle encore non reproduite** : la localisation précise de l'objet
`{ProductId, IndexExemplaire, DrmTechnlology, link}` n'est pas établie
dans les éléments expurgés disponibles. Il peut être dans la page privée
`MesLivres.aspx`, dans un composant de fiche possédée ou dans
`readBook.ashx`. Un script du dernier cas était **déjà** reconnu par
`delivery_url` dès lors qu'il ne contenait qu'une destination autorisée.
Ce correctif ne constitue donc pas une preuve de résolution réelle.

## Nouveau parcours sûr

1. Recherche, connexion et inventaire : `parse_library_page` ignore par défaut
   les objets JavaScript contenant les URL signées. `SearchResult` ne contient
   **aucune** URL ACSM ; aucun GET de livraison, ACSM ou téléchargement.
2. Sur **Télécharger** explicitement : `NumilogSession.download_direct`
   retrouve le `record_id` scoped du compte via une nouvelle lecture des
   pages privées ; elle active alors `delivery_evidence=True`.
3. Une URL de livraison n'est utilisable que lorsque l'objet JavaScript est
   un descendant structurel de **la même fiche DOM**, qu'une seule action
   `readBook.ashx` est présente dans cette fiche, qu'un **unique** objet
   `ProductId / IndexExemplaire / DrmTechnlology: ADOBE / link` existe, et
   que ses identifiants concordent avec `data-product-id` /
   `data-index-exemplaire` lorsque ces attributs sont attestés. Le contrat
   **n'assimile jamais** `ProductId` ou `IndexExemplaire` au `lid` HTTP.
   Un script hors fiche, une association contradictoire ou ambiguë n'autorise
   aucune livraison directe.
4. En présence d'une unique preuve scoped à fraîcheur de l'action : seul un
   lien `HTTPS entrepot2r.numilog.com:443` de chemin exact
   `/fulfillment/URLLink.acsm` est accepté. Aucun paramètre de signature
   n'est conservé en catalogue. Chaque saut réseau reste soumis à
   `checked_url`, à l'allowlist stricte et aux limites MIME/taille/redirect.
5. Sans preuve de fiche, le parcours historique `readBook.ashx` s'applique.
   Ses HTML intermédiaires à URL unique étaient déjà supportés. Un formulaire
   de connexion HTML 200, un HTML d'erreur, zéro lien, plusieurs liens,
   HTTP 410, HTTP 429 et XML non ACSM ont maintenant des codes de sortie
   distincts et non sensibles.
6. Les ACSM reçus et validés sont uniquement confiés à
   `acquisition_broker.import_acsm` avec l'identité propriétaire contrôlée
   par `identity_broker`. Aucun outil DRM interne nouveau, aucune
   acquisition planifiée Numilog (#1862).

## Diagnostic sûr pour lever l'incertitude

Événements déjà prévus dans les diagnostics du plugin :

- `bibliothèque livraison` : phase `owned_card`, pagination et nombre
  de fiches / liens structurés reconnus (jamais leur contenu).
- `livraison ACSM` : origine de la décision `owned_card` ou `readbook`.
- `livraison HTML` : catégorie `login / error / intermediate / invalid`
  et code `session_expired / html_error / missing / ambiguous /`
  `single_allowed_candidate`. Pas de chemin, query string, cookie, titre,
  `ProductId`, `IndexExemplaire` ou document HTML.

Pour déterminer la différence avec la page réelle, ne collecter
**que** : catégorie de page (MesLivres/fiche/readBook), support de l'objet
(script/attribut), **présence sans valeurs** des attributs de corrélation,
comptes de candidats, suites de statuts HTTP/MIME **sans URL**, et état
de connexion. Ni HTML privé brut, ni URL signée, ni jeton, ni identifiant
de commande ne doivent entrer dans les logs ou artefacts CI.

## Validation et limites

Les fixtures utilisent des comptes, numéros, signatures et réponses
**entièrement fictifs** avec `httpx.MockTransport`. Elles testent deux livres
achetés différents, une référence non possédée, les liens orphelins,
ambiguïtés, HTTP 403/410/429, redirections hôte/port interdits, XML trompeur,
login HTML 200, import ACSM unique, action ADEPT requise et suppression
des temporaires. Elles ne contactent pas Numilog.

La capture d'une **structure expurgée du HTML effectivement reçu en production**
et un essai manuel autorisé restent nécessaires avant de fermer #1883.
