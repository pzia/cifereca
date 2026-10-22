# Audits de bibliothèque

Le package 0.2.9 détaille les compteurs dans « Résultats… » avec un Core proposant
`MaintenanceResult.details`. Les anciennes versions compatibles conservent le
résumé et les avertissements. Les catégories peuvent se recouper ; leurs nombres
ne prouvent jamais une association ou une absence de livre.

Le package ajoute **Maintenance → Réconciliation de bibliothèque** pour
l'identité active. Il requiert le broker public optionnel
`library.reconciliation.read` livré par le Core dans #2607 ; sur un ancien Core,
ce seul outil reste indisponible, sans importer de code privé ni appeler un scan
de catalogue qui écrit en base.

La préparation ne scanne pas. L'exécution utilise la tâche Maintenance existante,
sa progression et son annulation, puis affiche uniquement des compteurs vérifiés :
fichiers observés/hachés, liens confirmés, correspondances bibliographiques,
droits non vérifiés et chemins enregistrés. Le résultat incomplet reste explicite.
Un droit non vérifié n'est pas présenté comme un livre absent ; un SHA ou un ISBN
ne prouve pas un droit. Aucun rattachement, téléchargement, interrogation ADEPT,
export privé ou réparation n'est effectué.

Le plan est local, lié à l'identité de préparation et à usage unique. Un autre
plan, une nouvelle préparation ou une désactivation l'invalide. Le Core revalide
l'identité active au moment du travail. Titres, chemins, SHA, comptes, prose libre
et champs inconnus ne sont jamais recopiés depuis la réponse du broker vers les
résultats Maintenance. Le changement effectif annoncé est toujours zéro.

Tests de ce package dans `tests/`, lancés depuis ce répertoire. Les brokers des
tests unitaires sont minimaux ; ils ne constituent pas une recette GTK de cette
nouvelle entrée. Les filtres par fournisseur/état, l'inspection locale privée et
les réparations appartiennent aux lots suivants de #2565.

Depuis le Core #2734, les détails de Réconciliation affichent aussi, si les
quatre agrégats sont présents et cohérents, le nombre de fichiers repris en
mémoire, inspectés pendant cet audit et revalidés après péremption. Chaque
nouvel audit relit SQLite/WAL et vérifie les chemins/fichiers ; ces nombres ne
prouvent aucun droit. Un ancien Core conserve les mêmes résultats sans ligne
de reprise. Aucune donnée privée du checkpoint n'est exportée.

Depuis la version 0.2.9, si le Core retourne une comparaison de deux audits
réalisés avec la même identité et les mêmes racines, les détails de
Réconciliation distinguent les changements de cohorte observée, de fichiers
et des cinq tables SQLite de preuve. Seuls des compteurs, booléens et libellés
fermés sont affichés ; les preuves privées et leurs empreintes ne sortent
jamais du Core. Une comparaison partielle n'atteste pas une couverture totale.
Les versions Core antérieures n'affichent pas cette ligne facultative.
