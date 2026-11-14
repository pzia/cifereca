# P1 #5540 — choix déclaratifs dans Paramètres

Le contrat public `AccountOptionField` expose, de manière **additive**,
`value_type="choice"` et `choices=((valeur, libellé), ...)`. Les providers
existants (`text`, `unsigned_integer`) ne changent ni données ni rendu.
Le producteur ne doit déclarer qu'une **préférence locale** ; une option
ne prouve jamais un quota fournisseur, une disponibilité ou une permission.

## Rendu et persistance

- L'éditeur générique utilise `Adw.ComboRow` et `Gtk.StringList`,
  sans code conditionné à l'identifiant d'un fournisseur.
- La ligne montre le **libellé** déclaré ; les contrôles de sauvegarde et
  de diagnostic comparent et sérialisent seulement la **valeur** déclarée.
  Un libellé ne devient jamais une valeur de configuration.
- Les champs restent dans `providers.accounts[].options` avec la portée
  existante `identity/provider/credential`. Les anciennes options opaques
  et les valeurs absentes avec défaut déclaré sont préservées.
- Les choix de déclaration doivent être un tuple de 1 à 32 couples
  `(token, label)`, avec tokens uniques, texte imprimable et borné, défaut
  explicitement présent. Les champs secrets ne peuvent pas devenir des
  listes visibles.
- Une ancienne valeur qui n'appartient plus aux choix rend le formulaire
  **non enregistrable** ; il ne bascule pas silencieusement au premier
  choix lors d'un changement de compte, d'un rebind ou d'un simple Save.
- La validation `build_settings_draft` rejette les valeurs non déclarées,
  mal typées et les schémas invalides avant toute écriture ; le rendu GTK
  et le draft réutilisent une validation du schéma commune, sans appeler le
  provider.

## Booléen d'opt-in : `value_type="boolean"` (#5540)

Le contrat public accepte désormais `AccountOptionField(value_type="boolean",
default="off")` pour une **préférence locale explicitement opt-in**.
La ligne de Paramètres est générée avec un `Adw.ActionRow` + `Gtk.Switch`,
sans test de l'identifiant fournisseur. Le switch conserve les mêmes
chaînes canoniques `"off"` / `"on"` que le type `choice` ; les booléens
Python/JSON, `"true"`, `"yes"`, les libellés et les valeurs inconnues
ne sont jamais convertis en consentement. Le défaut `"on"`, les champs
secrets et les choix mélangés à un switch sont **refusés** au rendu et au
draft ; une configuration antérieure non canonique marque le formulaire
invalide et ne se réécrit pas lors d'un Save indépendant.

Les options `text`, `choice`, `url` et `unsigned_integer` gardent leur
comportement. Aucun plugin n'est automatiquement migré vers `boolean`,
aucun appel de connexion ou de synchronisation n'est déclenché par
l'affichage ni par le retour de `get_text()`. L'activation réelle
requiert toujours les contrôles Core de consentement/identité/credential.
Tests : deux profils, comptes et options héritées, défaut OFF, validation
du schéma, type malformé, switch GTK incohérent, persistance exacte.

## Portée de la tranche

Les déclarations des plugins livrés restent inchangées. Le cas synthétique
`authenticated_sync: off/on` vérifie la mécanique **sans activer de SSO**,
et illustre comment un plugin peut migrer ultérieurement, par PR dédiée,
un texte opt-in vers un choix. Les champs `unsigned_integer` BnF, Paris
et La Rochelle gardent leur comportement existant.

Il reste à #5540 : inventaire/matrice des capacités des différents providers,
types URL/opt-in explicites, statut live distinct de la préférence locale,
présentation des comptes principaux/liés, et recette GTK des scénarios
multi-identités sur les plugins réels. Cette tranche ne prétend ni à un
nouveau contrôle métier ni à la clôture du parent.
