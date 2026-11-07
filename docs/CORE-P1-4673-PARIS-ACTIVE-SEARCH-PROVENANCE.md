# P1 #4673 — recherche Paris : provenance alignée sur l'identité active

> **Mise à jour P1 #4909 (07/11/2026) :** la dernière section
> « P0 : aucun repli vers un compte Paris explicitement étranger » est une
> politique historique **supplantée pour les notices bibliographiques
> uniquement**. Les recherches A depuis une session B produisent désormais
> une projection `shared_read_only`, sans données privées ni GET de droits.
> Les gardes d'emprunt et la priorité au compte propre restent impératives.
> Voir [contrat actuel #4909](CORE-P1-4909-SHARED-CATALOGUE-PARIS.md).
>

## Cause corrigée

Les chemins `search` (synchrone) et `search_progressive` conservaient auparavant **la première session connectée par `provider_id`**, même si cette session Paris appartenait à un autre profil. La notice reçue depuis Paris B conservait logiquement `source_credential=B` : le bouton Emprunter de A était ensuite **correctement refusé** par les gardes locales, alors que le compte A était connecté et éligible à la recherche. Changer l'ordre de connexion B/A modifiait la provenance. Il ne faut pas masquer ce défaut en supprimant la garde d'emprunt.

## Contrat corrigé

Pour un fournisseur donné, choisir la première session **active dont le credential est explicitement lié à l'identité sélectionnée** (`storage.identity_for_connection`) ; si aucun rattachement actif n'est attesté, conserver le premier fournisseur connecté pour les catalogues publics, **sans réécrire son `source_credential`**. Ne jamais mélanger deux sessions ou accorder l'éligibilité d'un autre compte. Conserver l'ordre fournisseur, les gardes de search lease, les générations de profil et le cache contenant le credential réellement sélectionné.

Le même helper est utilisé avant `_without_redundant_merged` et avant le calcul des clés de cache dans les deux parcours. Un changement de A→B→A produit trois recherches logiquement distinctes, chacune avec sa provenance exacte ; le cache existant partitionne par identité et par session (credential). Un résultat arrivé sous B ne devient jamais une possibilité de prêt A.

## Recette autonome

Tests hors réseau sur deux sessions Paris et deux profils : ordre de connexion inversé, `search` et `search_progressive`, A→B→A, seconde lecture avec cache, disparition de la session A et propriétaire du credential inconnu. Aucun POST, aucun simulateur de permission fournisseur, aucune donnée de compte privé.

**Limites :** la disponibilité affichée reste celle du catalogue sélectionné ; l'admission réelle côté fournisseur (quota, titre, restrictions, prêt en cours) ne peut pas être attestée par le seul `source_credential`. Les parcours d'emprunt interprofils #4662 et la capacité fournisseur compte-spécifique #3120 restent distincts.


## Complément P1 : plusieurs connexions Paris sur un même profil

La priorité « première session appartenant à l'identité active » ne suffit pas
si deux credentials Paris sont liés à cette identité. Lorsqu'une
`primary_paris_connection_id` explicite et syntaxiquement valide est définie,
les recherches synchrone et progressive privilégient **cette session connectée**
si son propriétaire SQLite est bien l'identité active, même si un autre compte
du même profil s'est connecté avant. Pour les autres fournisseurs et en absence
de primaire attesté et connecté, l'ordre historique de découverte subsiste.
L'identité/provenance du résultat n'est jamais réécrite.

Ce choix n'est **pas** une preuve de prêt : la garde
`_normal_paris_source_current` conserve tous les contrôles de connexion
activée, configuration et compte principal. Un primaire disparu laisse au
plus un catalogue sous sa provenance secondaire exacte ; aucune autorisation
de mutation ne lui est conférée.

Le primaire configuré est capturé par requête et contrôlé pendant les leases.
Son changement à chaud invalide les résultats en vol, y compris callbacks et
miroirs de recherche progressive ; la clé de cache continue à incorporer le
credential réellement retenu. Tests synthétiques : trois sessions B/A2/A1,
ordre inversé, changement de primaire sans reconnexion, cache, primaire
déconnecté et changement en cours de GET. Aucun I/O fournisseur réel.

Ce correctif ne qualifie ni quota, ni disponibilité de prêt par compte ; la
recette GTK réelle A→B→A et la décision d'éligibilité fournisseur restent au
suivi #4673/#3120, sans POST de diagnostic.

## P0 : aucun repli vers un compte Paris explicitement étranger

Si le profil actif A ne possède plus de session Paris connectée et que la
seule session disponible appartient explicitement à B dans SQLite, le Core
**n'interroge plus cette session Paris depuis A**. La provenance de B ne doit
pas apparaître sous A sous prétexte que la garde de prêt refuserait ensuite
l'action. La règle s'applique aux recherches synchrone et progressive,
indépendamment de l'ordre des connexions ; elle n'affecte pas les autres
catalogues publics ni le repli historique des sources sans propriétaire
attesté. Le droit de prêt reste contrôlé séparément et ne se déduit pas de
la recherche. Régressions synthétiques B seul, B→A, A→B→A et catalogue
public simultané, sans HTTP ni mutation fournisseur.
