# P0 #3121 — second EbookUrl absent, premier ACSM encore valide

Après un prêt Paris confirmé, la carte `ListLoans` peut ne plus exposer le
lien éphémère `EbookUrl`. Un SourceReference observable n'est pas, seul,
une permission de GET ni un fichier livré. Cependant, un premier ACSM
capturé dans le même clic et lié au claim `loan.borrow` peut encore être
réutilisable pour la livraison Core ; l'ancien préflight ne savait pas
qualifier l'offre sans seconde URL.

Le Core qualifie une offre **interne** `core.paris.firstacsm:<digest>` uniquement
pour un prêt DILICOM Paris du compte primaire, source actuelle attestée,
consentement persistant, serrure de Sync, édition/prêt uniques dans un
inventaire frais, reçu privé existant, et claim `loan.borrow` confirmé.
`acsm_cache.reuse_initial` vérifie le scope, la validité XML/SHA/TTL et
la concordance du checkpoint SQLite. Aucune URL signée ou identité n'est
rendue publique ; cet identifiant n'est qu'une admission descriptive.
Le moteur `complete_selected_delivery` revalide séparément le fichier
exact et la permission avant ADEPT et publication B.

Un reçu absent, invalidé, expiré, malformé ou d'un autre compte ne devient
pas une offre positive : pas d'Artifact, pas de droit nouveau, pas de
récupération ambiguë ni mutation fournisseur de secours.

Recette synthétique canonique :
`tests/test_core_p0_3121_canonical_first_acsm_flow.py` couvre notamment
l'absence d'EbookUrl (sans second GET) et le refus d'un reçu altéré.
