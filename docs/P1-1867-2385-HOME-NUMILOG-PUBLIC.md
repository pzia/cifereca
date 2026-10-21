# P1 #1867 / #2385 — Numilog : achats privés et catalogue public distincts

## Symptôme

Le plugin `provider.numilog` est une source de **livres achetés**,
adossée à un compte ; `numilog-catalog` est une source de **catalogue
commercial public anonyme**, `singleton_public`, volontairement
désactivée par défaut et dont `connect()` ne crée aucun `Account`.
L'Accueil affichait Numilog privé mais omettait totalement le public
activé, car son ensemble de sources publiques ne contenait pas
`numilog-catalog`.

## Contrat livré

L'Accueil classe désormais `numilog-catalog` dans les **catalogues
publics**, avec le libellé « Numilog — Catalogue public », distinct
du compte « Numilog » qui reste dans les **sources authentifiées**.
Le catalogue public n'est visible **que si le Core renvoie un
descripteur de session actif** dans `dashboard_state.sources` ;
une provenance historique `statistics.sources` peut rester affichée
comme origine d'un fichier, mais **ne réactive pas** le fournisseur
catalogue désactivé.

Ce regroupement **ne modifie pas** les providers techniques :
aucun deuxième compte Kobo n'est créé, aucune credential n'est
demandée pour Numilog public, aucune source privée n'est fusionnée
avec les notices commerciales. Aucune entrée publique ne possède
`owned=True`, aucun achat, emprunt, téléchargement ou droit
d'acquisition n'est déduit d'un catalogue.

La projection des sources de l'Accueil conserve seulement
`provider_id`, `label` et `connected`. Les champs de diagnostic
et URLs éventuellement présents dans une future projection Core
ne peuvent plus être copiés dans le modèle des sources de cette page.

Un profil **sans Account mais avec catalogue public actif** peut être
présenté honnêtement : « aucune source de prêt configurée » et
« Catalogue public », sans alerte de prêt ni demande de connexion.

## Preuves et limites

Tests synthétiques : Numilog privé seul, public seul sans compte,
les deux présents, public désactivé avec provenance historique
restante, statut de connexion, champs privés supplémentaires,
fournisseur inconnu. Le parcours GTK Accueil existant, à
1366/1600 px, ajoute « Numilog + catalogue public » et « catalogue
public seul » sans appel réseau.

Les dépendances et la fraîcheur du catalogue, les droits d'achat,
les limites HTTP Numilog et le regroupement UX Kobo « Mes achats /
Catalogue » restent hors de cette tranche. L'activation du fournisseur
public et sa qualification amont relèvent de #1886 (P0) ;
la capacité `catalog.refresh` n'est pas créée par cette présentation.
