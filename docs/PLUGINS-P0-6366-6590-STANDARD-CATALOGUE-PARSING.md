# #6590 — inventaire et réception du parsing catalogue standard

État inspecté : main `5d007b86`, 18/11/2026. Les preuves ci-dessous sont
des contrats et des fixtures contrôlées ; elles ne qualifient ni disponibilité
distante actuelle, ni autorisation, ni exhaustivité. Les gates des nouvelles
preuves restent à recevoir. Aucun changement de transport, droit, écriture C,
acquisition ou activation fournisseur.

| Fournisseur | Avant migration (inventaire original) | Sur main inspecté | Preuve / limite |
|---|---|---|---|
| EbooksGratuits | _BoundedOPDSTree / _xml_root | _xml_root délègue à parse_bounded_catalogue_xml ; _opds_fetch renvoie l'arbre ; navigation/next via catalogue_next | test_opds_calibre_1084.py : découverte + descriptor + deux pages, 4 réponses / 4 parsings attendus ; projection et pagination sur les arbres déjà lus. PR #6749 en réception pour les pertes de projection. |
| Gutenberg | _parse_feed et _next_page_url reparsaient séparément | parse_atom_catalogue dans le parcours Search ; feed réutilisé pour projection et next ; catalogue_next et resolve_catalogue_link communs | test_opds_failure_redaction_p1.py::test_search_single_parse_per_traversed_page : 2 pages / 2 parsings attendus ; façade historique indépendante. PR #6748 en réception. |
| Gallica | _parse_feed et _discover_search_template utilisaient ET.fromstring | Atom commun pour racine et réponse ; OpenSearch via XML borné ; template mémorisé ; politique URL/repli locale | test_opds_audio_regressions.py : découverte 3 réponses (2 Atom + 1 descriptor) ; seconde recherche 1 réponse/1 Atom attendue. PR #6747 en réception. |
| BnF-Prêt | parse_catalog recevait un Mapping | JSON borné depuis bytes, validation du graph déjà décodé, liens/next communs ; DTO et politique d'origine spécifiques | test_bnf_pret_opds2_2054.py : 1 décodage wire, 0 redécodage Mapping, projection égale. Contrat offline non enregistré comme provider ; #6746 en réception pour ambiguïtés et couverture partielle. |
| BnF Catalogue/SRU | XML/MARC spécifique | parse_records et probe âge emploient XML borné ; projection SRU/MARC et preuves d'âge spécifiques | #6668, #6670 ; #6738 en réception pour couverture de page. Pas de nouveau parser métier MARC générique ni attestation d'âge numérique par transfert. |
| Gallica SRU/audio | XML spécifique | primitive bornée ; récupération de records malformés explicitement distincte du parcours nominal | Les fragments récupérés ne valent pas un parsing unique ni réponse exhaustive ; récupération bornée, statut partiel. OAIRecord _audio_tracks relève de l'acquisition et n'est pas une migration catalogue. |

## Frontière des primitives

La primitive stdlib dans cifereca.plugin_api.catalogue_xml ne dépend pas de
Core, session, GTK, client réseau ni politique de droits. Retourne un arbre XML
une seule fois sans résoudre d'URL. DTD refusé y compris UTF-16 ; limites
d'octets, profondeur, éléments ; erreur fermée, pas de contexte privé.
La primitive Atom conserve les éléments et liens bruts ; les politiques de
navigation/acquisition, les namespaces supplémentaires, la complétude et les
DTO métier restent dans les adaptateurs. Le JSON borné refuse notamment
membres dupliqués, valeurs non finies, graphes cycliques et budgets dépassés.
La validation d'un graph déjà décodé n'est pas un décodage JSON supplémentaire.
catalogue_next distingue absence, invalidité, ambiguïté et destination valide
sous politique injectée. Un lien ne constitue pas un droit.

## Recette de parité

- tests/test_catalogue_xml_6590.py : UTF-8/UTF-16, DTD, HTML, budgets exacts,
  XML malformé et namespaces bruts ; tests/test_catalogue_json_6590.py :
  budgets et erreurs structurelles, duplication, graphes déjà décodés.
- EbooksGratuits : conserver EPub/PDF attestés, date de mise à jour distincte,
  navigation même origine et classification HTML/erreur XML ; stream borné
  et refus gzip/redirect étranger déjà dans test_opds_streaming_byte_budget_p1.py.
- Gutenberg : test_opds_failure_redaction_p1.py conserve le zéro Atom réel,
  HTML 200 et erreurs typées, refus next étranger, aucun retry lors d'un refus.
- Gallica : test_opds_audio_regressions.py conserve recherche EPUB, ARK exact et
  provenance de date dcterms:issued ; audio optionnel et récupération séparés.
- BnF-Prêt : test_bnf_pret_opds2_2054.py couvre groupes, navigation, liens
  host/path/query restrictifs, templates, erreurs HTTP, parité bytes/Mapping,
  couverture sans URL signée et absence de droit.
- Les compteurs ci-dessus sont des assertions sur fixtures, pas des mesures
  exécutées localement ni un benchmark avant/après sur un fournisseur réel.
  Le doublon Gutenberg constaté initialement ne doit pas être confondu
  avec la façade historique hors Search.

Réception : vérifier les gates exactes de la PR de preuve et des tranches
#6746–#6749/#6738, les intégrer sur main puis relever la parité combinée.
Les écarts de qualification réseau et de complétude réelles relèvent de #1917,
sans nouvelle campagne implicite. La clôture #6590 reste conditionnée à sa
réception propre ; aucune ouverture de nouveaux lots de parsing sans défaut
démontré.
