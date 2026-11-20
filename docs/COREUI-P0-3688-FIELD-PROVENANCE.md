# Provenance déclarée des champs du détail

Le contrat public existant `DetailField.provenance: Provenance | None` est
désormais conservé jusqu'au rendu commun `DetailPanel`. Il ne crée aucun
statut attesté, fraîcheur, droit ou rapprochement d'édition.

Pour un champ portant une `Provenance` typée, son infobulle et sa description
accessible contiennent la valeur complète puis les informations présentes :
source du champ, fournisseur déclaré, référence déclarée, observation déclarée.
La date reste exactement celle déclarée ; aucune conversion ou date courante
n'est ajoutée. Une provenance typée vide indique « Provenance non renseignée ».
Les anciens champs sans provenance gardent leur infobulle habituelle.

Les chaînes réutilisent la frontière de texte des métadonnées. Le dictionnaire
libre `Provenance.metadata` n'est jamais parcouru ni affiché. Aucune requête,
lecture de fichier ou résolution d'autorité n'est déclenchée par le rendu.

`Essentiel`, `Catalogue` et `Détail` gardent leurs affordances existantes.
Les autres sections et les sections d'enrichissement depuis le cache reçoivent
leurs champs pour afficher la provenance, avec `field_actions=False` : une
provenance ne doit pas activer un bouton de recherche ou de copie auparavant
absent. La provenance globale et la fraîcheur d'une contribution du cache
restent séparées de la provenance de chaque champ.

Deux champs de même libellé/valeur avec des sources différentes restent deux
lignes. Leur conflit n'est ni arbitré ni transformé en permission par GTK.
La copie d'une métadonnée conserve seulement sa valeur, pas son infobulle.

Réception partielle #3688 sous #6366 : tests du helper, rendu partagé et
parcours GTK existant (champs répétés, sections passives, cache). La valeur
transmise à la description accessible est testée unitairement ; le parcours
GTK vérifie les lignes mappées et infobulles, sans lecture AT-SPI. La production
de provenances/états depuis toutes les sources C et l'inventaire complet des
badges six vues restent à recevoir. Aucun nouveau DTO ni aucune release.
