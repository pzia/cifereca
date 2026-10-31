# P1 #976 — revue locale des SourceReference et OriginLink ADEPT

Le contrat #3171 impose de **conserver séparément** la source du droit Paris,
la preuve de livraison et la preuve d'activation Adobe. Un profil ADEPT ne
devient pas propriétaire d'un droit Paris. Une bibliothèque cible et un SHA
identique ne transmettent ni compte ni licence.

## Sonde Core en lecture seule

`cifereca.core.adept_canonical_origin_review.review_paris_canonical_adept_links`
lit le schéma canonique v14, uniquement pour `loan_id` et `item_id` exacts
(les colonnes `right_id` et `edition_id` de SourceReference). Elle ne fait
**aucun** appariement par titre, ISBN, chemin, SHA ou identité courante.

Pour chaque SourceReference `provider_id=paris` et `reference_kind=loan`,
elle montre séparément : compte et connexion Paris, branche, identité de
bibliothèque, liens fournisseur et liens `adept` pour **ce** couple
`source_reference_id` / Artifact. Les liens d'un autre droit ou d'une autre
identité ne peuvent jamais compléter les preuves manquantes.

- `paired_links_unverified` : deux OriginLink persistés (provider et adept)
  portent le même SourceReference/Artifact/identité. Cela **ne prouve pas**
  quel profil Adobe a effectivement activé la licence, ni son état serveur.
- `unpaired_or_missing_links` : une des deux origines est absente **ou**
  le lien ADEPT/fournisseur existe sans véritable référence de reçu
  (`evidence_ref` vide ou uniquement des espaces) ; ne jamais inférer sa
  présence depuis un autre prêt portant le même SHA. Le diagnostic expose
  `links_without_receipt` par Artifact et le motif de revue
  `canonical_origin_receipt_missing`, sans recopier le reçu lui-même.
- `conflicting_records` : liaisons inter-identités, plusieurs références
  ADEPT pour le même Artifact ou plusieurs Artifacts au sein d'une même
  source, nécessitant une revue de transaction.
- `unknown` : aucune SourceReference exacte ; les anciennes acquisitions
  ne sont pas automatiquement promues en autorité canonique.

Les statuts `return_authorized=false` et
`adobe_profile_resolution=unknown` sont invariables. Les références de
reçus sont conservées en base mais **jamais** projetées par cette sonde.
Les OriginLink ADEPT canoniques actuellement persistés décrivent l'identité
de bibliothèque, le SHA et la référence du reçu, **pas** nécessairement le
profil Adobe réel. Un inventaire ADEPT historique externe reste une preuve
distincte, qu'un résolveur P1 devra rapprocher par identifiants transactionnels
exacts, avec contrôle humain explicite.

Cette sonde ne crée aucun lien, ne contacte aucun fournisseur, n'ouvre aucun
fichier ebook et ne restitue ni ne rejoue un ACSM. Elle se distingue des
commandes `paris-adept audit` et `audit-origin` fondées sur les reçus
historiques : aucune équivalence n'est inventée entre leurs identifiants
et les SourceReference v14. L'exposition CLI de cette sonde et la recette
terrain seront livrées séparément après validation du contrat.
