# Baen — provenance des liens publics (0.1.11)

Le plugin Baen accepte seulement les pages et téléchargements liés à
l'origine HTTPS exacte `www.baen.com` (port par défaut ou 443) :

- les URL de fiches provenant des cartes de recherche sont filtrées ;
- les URLs de fiches demandées par `holdings()` et `download_direct()`
  sont revérifiées **avant** tout GET ;
- l'EPUB indiqué dans le HTML d'une fiche n'est transmis à
  `transport.http` que si sa destination reste sur la même origine ;
- userinfo, autre schéma/hôte/port, fragments, espaces et contrôles bruts
  ne peuvent définir un destinataire réseau ;
- le client HTTP ne suit plus automatiquement un 30x : un redirect
  renvoie `Baen : public_redirect_unverified` plutôt que d'autoriser
  une requête vers une origine non attestée.

La politique est conservatrice : une URL EPUB CDN externe ou une redirection
légitime devra être **qualifiée** par des preuves réelles et ajoutée
explicitement ; il ne faut pas élargir par un wildcard sans audit.
Les routes de catalogue Baen restent à requalifier sur pages amont actuelles
(cf. PR expérimentale #1057) ; les tests synthétiques ne prouvent ni la
disponibilité du service, ni un téléchargement réel.

Tests hors réseau : `tests/test_trusted_url_p1.py`. Aucun changement au
contrat Core/GTK ni aux droits d'un compte. Suivi #1917.
