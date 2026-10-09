# Jeunesse — Usborne

Source spécialisée pour #741.

Le catalogue public Usborne publie un âge minimum directement dans ses listes **Browse by Age** (`Age: 3+`, `Age: 10+`, `Age: 18+ months`, etc.). Le plugin ne déduit jamais un âge depuis le thème, la collection ou le niveau Lexile.

## Navigation

- pages publiques `/us/books/browse-by-age` ;
- catégories 0–3, 3–5, 5–7, 7–11, 11–13, 13+ ;
- pagination explicite `?p=N` ;
- maximum 40 résultats par appel ;
- aucun crawl complet.

`search(query)` ne suppose aucun endpoint de recherche libre : il charge une page Browse by Age explicitement demandée puis applique seulement un filtre texte local.

## Âges

Les années sont conservées comme âge minimum éditeur :

- `Age: 3+` → `age_min=3` ;
- `Age: 10+` → `age_min=10`.

Les âges en mois ne peuvent pas être représentés exactement par le filtre annuel actuel de Cifereca. Le plugin applique donc une projection **conservatrice** vers l’année entière supérieure, tout en conservant le libellé et le minimum source en mois :

- `Age: 6+ months` → `age_min=1`, libellé original + `age_min_months=6` ;
- `Age: 18+ months` → `age_min=2`, libellé original + `age_min_months=18`.

La confiance de cette projection annuelle est abaissée à 0,8. Les valeurs en années restent à 1,0.

## Preuves bibliographiques

Les URLs produit Usborne contiennent un ISBN-13. Le plugin le valide et le publie comme identifiant bibliographique fort, ce qui permet le rapprochement avec les autres sources Jeunesse sans transfert titre/auteur.

## Réseau

- HTTPS uniquement ;
- hôtes `usborne.com` / `www.usborne.com` ;
- pages Browse by Age uniquement ;
- redirections désactivées ;
- proxy d’environnement désactivé ;
- HTML limité à 2 Mo ;
- timeout borné.


## Durcissements 0.2.0

- curseurs limités strictement à 1–100 ; aucune correction silencieuse de `0` ou `101` ;
- page suivante acceptée uniquement si le lien **Next Page** pointe vers `current+1` ;
- réponses HTTP 3xx rejetées ;
- chemin réseau borné exactement à `/us/books/browse-by-age` et ses sous-chemins, sans simple préfixe ambigu ;
- filtre texte local borné à 300 caractères ;
- les doublons de liens/badges ne déplacent plus la frontière de carte lorsque cela ferait perdre l'âge publié ;
- règles ISBN, âge en mois, HTTPS et absence de recherche distante inventée inchangées.
