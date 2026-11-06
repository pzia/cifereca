# P1 #4530 — pilote de qualification BnF SRU MarcXchange (sans réseau)

## Périmètre livré
`bnf_age_evidence_probe.py` examine **exclusivement** une réponse XML SRU déjà récupérée et bornée. Aucun changement du provider livré, aucune recherche supplémentaire, aucun droit d'accès ou transfert d'âge vers une édition distincte.

Le SRU BnF annonce les schémas `unimarcXchange` et `intermarcXchange` en MarcXchange 2.0. L'ISBN d'une notice est en `010$a` (UNIMARC) ou `020$a` (Intermarc). Le pilote sélectionne uniquement les notices bibliographiques dont les ISBN propres sont tous valides et équivalents à l'ISBN demandé (checksum vérifié, ISBN-10 converti en 13). L'ARK et les champs bruts associés au CNLJ sont conservés **sans les confondre** : `329` UNIMARC = **commentaire critique** (pas un âge), `333` UNIMARC = **note sur le public destinataire**, `833` Intermarc = **zone mixte** (critique/public destinataire/genre). La sortie `evidence_role` rend cette distinction explicite. Source : documentation BnF UNIMARC Bibliographique, 329/333, et documentation SRU BnF pour 833.

**Limite volontaire :** l'existence d'un champ candidat ne prouve ni l'âge numérique, ni la signification de la sous-zone. La sortie indique `unqualified_raw_evidence`. Les fixtures sont synthétiques, pas des captures de la BnF. Une qualification sur réponse SRU publique réelle est requise avant tout mapping âge, toute publication de couverture, ou tout usage du champ dans les filtres.

## Recette publique bornée (à documenter, non exécutée par le pilote)
Pour un ISBN d'édition explicitement choisi, interroger uniquement l'index `bib.isbn`, `maximumRecords` borné, `recordSchema=unimarcXchange` puis éventuellement `intermarcXchange` ; vérifier statut HTTP, nombre de résultats et ARK. Ne retenir que le `010$a` / `020$a` propre, jamais un ISBN associé; ne pas inférer la même édition sur titre/auteur. Expurger les retours avant dépôt en CI. Noter pour chaque échantillon le nombre de réponses, ISBN propre concordant, champs bruts, absents, doublons/ambiguïtés, schéma, date, langue/support. Aucun GET par résultat à l'application d'un filtre.

## Références
- https://api.bnf.fr/fr/api-sru-catalogue-general
- https://api.bnf.fr/fr/node/2688
- https://www.bnf.fr/fr/service-sru-catalogue-general-de-la-bnf

## Suites
1. Qualifier les sous-zones et codes d'âge sur un échantillon SRU public **réel**, y compris des notices sans âge et des formats différents ; conserver les exemples sous licence et expurgés.
2. Valider séparément Intermarc `833`, UNIMARC `329`/`333` et `JAg`, y compris les codes « à partir de » et les valeurs non numériques ; ne pas convertir `JAg1399` en 13–99.
3. Mesurer la couverture par édition et fournisseur avant de proposer l'intégration à la consolidation C (#4552/#2385). Pas de PU requis pour cette qualification technique.

Sources complémentaires : [format UNIMARC BnF](https://www.bnf.fr/sites/default/files/2023-10/UNIMARC_B.pdf), [documentation SRU](https://api.bnf.fr/fr/api-sru-catalogue-general), [référentiel JAg](https://data.bnf.fr/en/vocabulary/age-jn/).
