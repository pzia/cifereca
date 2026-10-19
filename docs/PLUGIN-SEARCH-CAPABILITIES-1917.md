# #1917 — Matrice des capacités de recherche des providers

État du code `main` observé le **19 octobre 2026**. Cette matrice décrit les
**contrats déclarés et les branches Python inspectées**, pas une recette HTTP
live ni une garantie d'exhaustivité du catalogue. Le ticket #1917 reste ouvert
jusqu'aux recettes multi-provider, Core/UI et gates demandées.

**Vocabulaire** — `serveur` = sélecteur transmis à l'endpoint ; `page locale`
= post-filtre appliqué seulement aux éléments récupérés ; `global` =
recherche textuelle générale sans facette dédiée ; `non attesté` = ne pas
présenter comme recherche exacte. Une page vide ne prouve jamais l'absence
d'un livre sur toutes les pages ou dans un autre catalogue.

| Provider / code source | Champs effectivement exposés | Couverture des champs | Pagination déclarée | Limites vérifiées côté code |
| --- | --- | --- | --- | --- |
| [BnF Catalogue général](../src/plugins/provider-bnf-catalogue/cifereca_bnf_catalogue.py) | all, title, author, isbn, subject, series, collection | SRU serveur pour les cinq premiers ; `series/collection` via requête générale SRU + post-filtre **page locale** | page / 50 | Scopes `all/physical/digital` ; supports filtrés localement sur les notices retournées ; distinct du prêt BnF. |
| [Gutenberg](../src/plugins/provider-gutenberg/cifereca_gutenberg.py) | all, title, author, subject, language | OPDS recherche générale, spécialités en **page locale** | next-link / 25 | Pas de résultat exhaustif par champ ; `bookshelf:` est une syntaxe générale distincte. |
| [Gallica](../src/plugins/provider-gallica/cifereca_gallica.py) | all, title, author, subject, isbn | `all` : OPDS/OpenSearch EPUB ; spécialités via SRU ; audio SRU sur branche indépendante | page / 50 par branche | Audio optionnel ; erreurs de branche rapportées comme partielles, pas comme absence de livre. |
| [Ebooks libres et gratuits](../src/plugins/ebooksgratuits/cifereca_ebooksgratuits.py) | all, title, author, subject, category, genre | Calibre OPDS/OpenSearch : recherche générale en amont, spécialités en **page locale** | page / 50 via `next` | `subject` élargit catégories + genres ; **sur main, `genre` inclut aussi les catégories** (écart ; correction proposée #2303, non présumée fusionnée) ; aucun crawl implicite. |
| [Baen Free Library](../src/plugins/baen/cifereca_baen.py) | all | Formulaire catalogue **global**, pas de champ auteur/ISBN | page / 50 | Champ inconnu rejeté ; état HTTP 401/403/429/5xx typé. Structure HTML amont encore à qualifier (#764). |
| [Bibliothèques de Paris](../src/plugins/provider-paris/cifereca_paris.py) | all | Syracuse texte **global**, aucun champ auteur/ISBN/subject attesté | page / 50 | Scopes `all/digital/physical`, erreurs partielles de branche ; droits de prêt séparés du catalogue. |
| [Kobo / Vault](../src/plugins/provider-kobo/cifereca_kobo.py) | all | Requête globale Vault ; sémantique serveur auteur/ISBN non attestée | page / 50 | `catalog` force l'appel public ; `all/digital` incluent bibliothèque uniquement si compte attesté, repli privé 401/403 vers public avec `partial_results` ; garde de session supplémentaire proposée dans #2300. |
| [CifericaOPDS virtuel](../src/plugins/cifericaopds/cifereca_cifericaopds.py) | HTTP : all ; fixture : all, title, author, isbn, language | HTTP candidat : `q/source_id` uniquement ; fixture : post-filtre sur jeu borné | none | Pas de pagination serveur HTTP attestée ; aucun compte ou acquisition. |
| [Numilog achats](../src/plugins/provider-numilog/cifereca_numilog.py) | all, title, titre (privé) ; `catalog` + all (public) | `all/digital` : titres possédés, filtre local ; `catalog` : catalogue commercial brSimple anonyme | page / 50 privé ; grille publique `GRID_PAGE` / 20 | **Deux autorités distinctes** ; seul le compte privé atteste `owned`, jamais le catalogue commercial (#1886, #1072). |
| [Numilog Catalogue public](../src/plugins/provider-numilog/cifereca_numilog_public_provider.py) | all | brSimple texte général, filtres de facettes suivis uniquement depuis des liens issus de la page | page / 20 | Singleton public désactivé par défaut ; accessibilité live, robots/CGU, pages ultérieures non qualifiés (#1886). |

## Autorité des droits et erreurs

- `Kobo` : une même enseigne peut exposer un catalogue public et une
  bibliothèque personnelle via une session, **sans** rendre l'entrée catalogue
  détentrice de droits. `Numilog` : deux providers distincts, mais une seule
  autorité d'achat (`provider.numilog`). Aucun `owned` n'est inféré d'un
  ISBN, d'un titre, d'une couverture ou d'une fiche marchande.
- Champs/scopes inconnus : erreur explicite `unsupported_field` /
  `unsupported_scope` lorsque le provider ne peut pas la satisfaire.
  Une erreur réseau ou HTTP 401/403/429 n'est pas un vrai zéro. Les sources
  avec filtrage de page doivent signaler `partial_results`.
- La recherche `all` **ne** signifie **pas** que tous les titres,
  contributeurs, sujets, éditions, identités et pages ont été parcourus.
  `field=author` sur Baen/Paris n'est pas une recherche exacte autorisée.
- Aucune recherche publique ne doit créer prêt, réservation, achat,
  ACSM, téléchargement, callback ou liaison à un profil ADEPT.

## Recette encore ouverte (ne pas déclarer succès)

Tester sur les providers précités : zéro réel, page suivante, champ
spécialisé, erreur 401/403/429/5xx, branche privée refusée, deux comptes,
source publique désactivée, déduplication sans mélange de droits, et
avertissements fédérés lisibles côté Core/UI. Les tests hors réseau du
répertoire `src/plugins/*/tests` et les corrections proposées #2300,
#2301 et #2303 (ne pas les présumer fusionnées) couvrent des barrières **partielles**, pas cette recette
transversale. Ne pas confondre qualification code/fixture avec une
observation HTTP live.
