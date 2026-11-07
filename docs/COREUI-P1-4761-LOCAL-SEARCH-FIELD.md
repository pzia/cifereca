# P1 #4761 — Champ de recherche partagé, vue locale Recommandations

## Contrat livré

Le filtre **Champ** de Recherche, déjà présent dans `QueryControls`,
est réutilisé par `LocalQueryControls` en **opt-in** via
`enable_field_filter()`. Les autres consommateurs de filtres locaux
conservent le champ masqué et la sémantique historique. Le plugin
Recommandations n'importe aucun module `cifereca.gui` : il consomme
seulement la façade publique `host.build_local_query_controls(...)` et les
méthodes facultatives `enable_field_filter()` et `filter_field()`.

La recherche textuelle opère **exclusivement sur la liste de
recommandations déjà calculées**. Le changement de champ, de texte, ou
sa remise à `Champ` ne provoque **aucun nouveau classement, accès disque,
scan de bibliothèque, requête catalogue, emprunt, téléchargement ou LLM**.

* `Champ` : comportement existant, titre et auteurs attestés (plusieurs
  auteurs pris en charge), ordre diversifié inchangé.
* `Titre` / `Auteur` : valeurs publiques exactes du candidat.
* `ISBN` : seuls les identifiants de livre dont la somme de contrôle a
  été vérifiée par le moteur ; un `record_id` fournisseur n'est jamais
  considéré comme un ISBN.
* `Sujet` / `Série` / `Collection` : projections bornées des seules
  métadonnées déjà en mémoire ; aucun objet fournisseur sérialisé.
* `Langue` : uniquement les codes normalisés et attestés déjà servis
  au filtre multi-langues.
* Champ mal formé : refus conservateur, aucun élargissement silencieux.

Le compteur visible inclut le champ explicitement choisi, en utilisant
le label du même widget Recherche. L'état vide est le comportement partagé
de la liste, et le filtre reste réversible par sélection `Champ` ou
effacement du texte.

## Recette et frontières

`e2e/recommendations_detail_probe.py` vérifie le vrai widget GTK aux
largeurs 1366 et 1600 : recherche locale par sujet, passage au champ
Titre (aucun résultat), reset, compteur associé et absence d'appel
fournisseur ou de recalcul. Les tests Plugins couvrent chaque champ
attesté, des métadonnées malformées, les homonymes, la réversibilité,
l'ISBN et la protection contre la sérialisation d'objets.

La matrice visuelle **Recherche ↔ Recommandations** aux largeurs et thèmes
demandés dans #4761, la recette clavier #774 et les actions métier #4664
restent **distinctes**. Ne pas fermer #4761 sur cette tranche.
