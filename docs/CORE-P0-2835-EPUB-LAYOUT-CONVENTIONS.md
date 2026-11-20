# #2835 — EPUB : conteneur, layout déclaré et convention de livraison Kobo

Le conteneur EPUB ne suffit pas à établir la présence des structures KEPUB.
Le suffixe ne prouve ni layout, ni origine, ni droit. Les métadonnées OPF
explicitement cohérentes donnent une indication descriptive, pas une
certification du viewport, des pages XHTML, du CSS ni du rendu.

Référence : [Kobo EPUB Guidelines — Sideloading for Testing Purposes](https://github.com/kobolabs/epub-spec#sideloading-for-testing-purposes),
consultée le 20/11/2026. Sur Kobo eInk via USB, ADE est le moteur par défaut ;
les suffixes composés demandent WebKit Kobo et, pour FXL, sa convention fixe.
Les notes et marque-pages peuvent être indisponibles sur ces exports, de même
que certaines miniatures. Aucun appareil ni firmware n'est attesté par cette
documentation seule.

## Matrice factorisée de 45 situations

Chaque cellule ci-dessous s'applique aux **cinq origines** de la seconde
table (9 combinaisons × 5 origines). Ces tableaux prescrivent les
comportements attendus ; ils ne revendiquent pas 45 parcours intégrés validés.

| Suffixe sélectionné | Layout fixe déclaré | Layout reflowable déclaré | Layout indéterminé |
|---|---|---|---|
| .epub | ADE par convention ; rendu fixe non attesté | ADE par convention ; rendu non attesté | ADE par convention ; layout indéterminé |
| .kepub.epub | WebKit Kobo ; vérifier convention/layout | WebKit Kobo ; structures KEPUB non attestées | WebKit Kobo ; layout et structures non attestés |
| .fxl.kepub.epub | WebKit Kobo fixe ; structures et viewport non attestés | WebKit Kobo fixe ; incohérence à vérifier | WebKit Kobo fixe ; convention à vérifier |

Dans chaque cellule : aucune transformation/écriture par la revue, aucune
attribution de droit, aucune sélection automatique. Les deux conventions
KEPUB comportent l'avertissement notes/marque-pages.

| Origine | API / catalogue attendus | UI et fichier attendus |
|---|---|---|
| Sync Kobo | Identité de source et SHA/Placement exacts ; pas de preuve par suffixe | Acquisition autorisée en Acquisitions ; présence exacte fraîche dispense de GET, sinon GET de repli autorisé ; pas de renommage implicitement vers KEPUB |
| Import | SHA + Placement + origine import/manual ; aucun droit fournisseur inféré | Conserver convention d'entrée ; layout inconnu si non vérifiable |
| Variante | Nouveau SHA si octets changés, OriginLink derived exact ; conteneur annoncé par le transformeur | L'optimizer générique annonce EPUB et termine par .epub ; aucune prétention de transformation KEPUB/FXL |
| Renommage | Même Artifact/SHA, relocation gardée, pas de nouvelle origine | Préserver suffixe composé, aucune perte de fichier ni modification du contenu ; mismatch signalé |
| Transfert USB | Réservation SHA + suffixe exact du format sélectionné, compatibilité déclarée distincte | Revue locale bornée et non exécutable ; affiche layout déclaré + convention sur Kobo eInk ; aucune conversion, ni validation du rendu ni droit par la revue |

## Preuves et limites

- Le détecteur OPF, le découpage du suffixe composé, les gardes de nommage,
  les propositions USB et les tests d'optimiseur existants restent les seuls
  moteurs. La projection USB ne décide pas du moteur ; elle explique
  la convention d'un chemin déjà réservé. Si la revue n'a pas observé le
  layout ou si la convention n'est pas cohérente avec son motif, ne pas
  afficher une qualification positive.
- Les tests de projection couvrent les neuf couples format/layout avec
  octets EPUB synthétiques, SHA et contrôle de contenu réel, puis projection
  de résultat USB. Les six contre-exemples refusent un label moteur sur
  preuve absente, inconnue, étrangère à la convention ou bloquée.
- Le catalogue historique regroupe encore les suffixes .kepub.epub et
  .fxl.kepub.epub sous kepub ; il ne faut pas renommer le fichier ou
  réinterpréter artificiellement l'Artifact global à partir de ce champ.
  La convergence import/catalogue/placement vers une projection de
  convention exacte reste à instruire. Aucun changement de schéma ici.
- Les autres origines disposent de tests de contrat existants, mais cette
  tranche ne démontre pas encore tous les chemins acquisition/import/reprise
  et la préservation effective du viewport par l'optimizer. Pas de recette
  de rendu physique en CI. Pour une Clara BW disponible : comparer, sur une
  copie synthétique non protégée et hors bibliothèque, les mêmes octets et
  les trois suffixes, vérifier page entière, RTL/navigation, notes et
  marque-pages ; noter firmware et échec éventuel. Aucun DRM nouveau ni
  mutation de bibliothèque utilisateur.


## Réception complémentaire (#6963, #6965 et recette canonique #2835)

- #6963 : fixtures EPUB réelles, suffixes composites, fixed/reflowable ;
  ressources OPF/XHTML/CSS/viewport/RTL préservées lors de l'optimisation,
  sortie EPUB générique. Le résultat n'atteste pas le rendu Kobo.
- #6965 : pour même SHA/taille, un index fondé sur le nom ne réécrit pas
  un format EPUB-family global déjà enregistré. La convention descriptive
  est projetée depuis le chemin du Placement present, indépendamment du
  format. Ni convention ni état SQL ne valent preuve physique ou droit.
- La recette `test_core_p0_2835_canonical_suffix_reception.py` exerce le
  vrai writer canonique, le scanner et Sync sur trois suffixes de placements
  déjà livrés, puis le contre-exemple d'une copie locale FXL non liée :
  présence exacte implique zéro GET ; doute avec offre et consentement
  valides implique GET, publication séparée et éventuel dédoublonnage
  ultérieur. Aucun renommage de convention automatique et aucun backup
  Kobo historique réintroduit.
- La recette physique Clara BW demeure conditionnelle à un appareil
  disponible. Les fixtures ne valident ni les moteurs d'un firmware réel
  ni une conversion KEPUB ; aucun changement de capacités USB.
