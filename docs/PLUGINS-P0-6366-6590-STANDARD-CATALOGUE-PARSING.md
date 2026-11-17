# #6590 — inventaire du parsing catalogue standard

Statut : tranche XML commune et premier adaptateur EbooksGratuits ; gates en cours.
Aucun nouveau transport, URL autorisée, droit, écriture C ou découverte.

| Fournisseur | Parseur constaté sur main | Suite de migration |
|---|---|---|
| EbooksGratuits | _BoundedOPDSTree / _xml_root, OPDS Atom et OpenSearch ; flux 2 MiB identity, 12000 éléments, profondeur 96, URL même origine et preuves d'acquisition distinctes | Primitive commune utilisée ; garder les politiques et DTO actuels, distinguer HTML et erreur XML |
| Gutenberg | _parse_feed et _next_page_url appellent ET.fromstring séparément ; transport OPDS 2 MiB, refus des next étrangers/ambigus, placeholder no records, provenance dates Atom vs Dublin Core | Partager une racine et les limites ; parité erreurs/redaction/pagination avant migration |
| Gallica | _parse_feed et _discover_search_template appellent ET.fromstring ; OPDS EPUB, branche SRU/audio séparée, repli template existant et dates spécifiques | Migration Atom/OpenSearch indépendante, conserver la policy de repli attestée ; qualifier SRU ultérieurement |
| BnF-Prêt | cifereca_bnf_pret_opds2.parse_catalog reçoit un Mapping ; métadonnées, groupes, liens, navigation, templates ; host/path/query allowlist et erreurs de statut ; contrat offline | Primitive JSON bornée puis DTO communs ; garder ses contraintes et statut de preuve. Pas de changement d'accès |
| BnF catalogue/SRU | XML/MARC et preuves d'âge #4530 déjà qualifiées sur échantillons | Extension après équivalence des budgets, namespace et encodages ; ne pas modifier l'attestation MARC |

La primitive stdlib dans cifereca.plugin_api.catalogue_xml ne dépend pas de
Core, session, GTK, client réseau ni politique de droits. Retourne un arbre
XML une seule fois, sans résolution d'URL et sans interpréter ses namespaces.
Le DTD est refusé par callback du parseur (y compris UTF-16), limites en
octets/profondeur/éléments appliquées avant et durant parsing, erreur fermée
sans contexte de parseur. L'HTML ne devient jamais zéro ou une preuve C.
Les budgets sont abaissables mais pas extensibles sans nouveau contrat.
HTTP buffering, compression, URL/redirect et contenu autorisé restent
responsabilité de l'adaptateur.

EbooksGratuits préserve les noms des constantes, _xml_root, les messages
et la classification de XML malformé / HTML / refus sécurité. Deux choix
plus conservateurs : payload vide refusé comme non qualifié, HTML valide
refusé dès la primitive (auparavant au contrôle du type de racine). Ne pas
promouvoir une erreur d'encodage non supporté en panne réseau. Tests de
parité plugin existants requis ; tests unitaires synthétiques ne qualifient
pas un nouveau fournisseur.

Étape suivante : DTO Atom descriptifs, liens bruts avec acceptation
injectée, next absent/invalid/ambigu ; ensuite migration Gutenberg et
Gallica une à une. Les fixtures fournisseur et décisions de complétude,
notamment placeholder Gutenberg et navigation mixte EbooksGratuits,
restent la propriété des adaptateurs. Pas de conclusion live sans preuve.
