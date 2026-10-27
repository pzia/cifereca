# Core P1 #1916 — bail de source lors d'une recherche synchrone

Le garde de génération de #3378 protège les résultats contre un changement
de profil A→B→A et contre une recherche plus récente. Il ne suffisait pas
quand **une session fournisseur était remplacée, déconnectée ou changeait
de credential pendant un `session.search` bloquant**, sans changement de
profil : l'ancienne session pouvait ensuite alimenter `last_search`, la
projection SQLite ou le cache, comme si sa connexion existait toujours.

Chaque recherche synchrone **et progressive** capture désormais la **liste de ses sessions
effectivement sélectionnées** avec leur triplet mémoire
`provider_id / credential_id / connected`. Après la découverte, après
chaque I/O et avant publication/cache, le Core exige :
- le même objet session encore enregistré dans le registre ;
- les mêmes coordonnées fournisseur/credential et `connected is True` ;
- la même génération de requête et le même objet identité.

Une reconnexion avec les mêmes paramètres ne réhabilite jamais les résultats
d'une ancienne instance. Les valeurs de provenance sont lues **directement
depuis la mémoire d'instance**, sans getter de plugin, et ne sont ni écrites
dans les diagnostics ni exposées à l'UI. Un bail synchrone révoqué rend `[]` ; le parcours progressif\nannule ses lots et événements non publiés (`summary.cancelled=True`),
n'ajoute pas de notice et n'envoie aucune requête supplémentaire.

Cas offline dans
`tests/test_core_p1_1916_sync_search_source_lease.py` :
remplacement de session même provider+credential, credential muté,
déconnexion, remplacement pendant lecture cache, disparition durant
découverte et source stable.

**Non couverts ici** :
états complets live/fallback/stale et UX de panne publique #1916. Le bail
n'autorise ni téléchargement ni changement de droits.
