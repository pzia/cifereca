"""#7092 contextual provenance presentation, without GTK or private Core imports."""
from cifereca.plugin_api.local_origin_detail import (
    LocalOriginDetailPage, LocalOriginPublicReport, LocalOriginPrivateReport,
)

# The generic book-review host accepts 12 actions including preview.
# Keep the same limit locally and report any truncated choice window.
_MAX_CATALOGUE_ACTIONS = 11

_LABELS = {
    "title_related": "Titre voisin",
    "title_concordant_author_unknown": "Titre concordant, auteur inconnu",
    "title_concordant_author_divergent": "Titre concordant, auteur divergent",
    "title_author_concordant": "Titre et auteur concordants",
    "isbn_title_author_concordant": "ISBN déclaré, titre et auteur concordants",
    "isbn_claim_ambiguous": "ISBN déclaré ambigu",
    "isbn_claim_divergent": "ISBN déclarés divergents",
    "title_concordant_author_unverified": "Titre concordant, auteur non vérifié",
}


def known_rows(page):
    lines = [
        ("Portée", "Notices publiques connues uniquement. Aucune origine ni droit attestés."),
        ("Couverture", "Partielle ; résultats relus indépendamment et susceptibles d’évoluer."),
        ("Page", str(page.page)),
        ("Résultats connus", str(page.known_matches)),
    ]
    for hit in page.matches:
        lines.append((hit.title, " · ".join(x for x in (
            hit.author, hit.provider_id, hit.record_id, hit.edition_id,
            _LABELS.get(hit.comparison, "Comparaison descriptive"),
            "Notice ancienne" if hit.stale else "Notice en cache",
        ) if x)))
    if not page.matches:
        lines.append(("Résultat", "Aucune correspondance connue ; absence non prouvée."))
    if page.has_more and not page.can_continue:
        lines.append(("Limite", "Limite de revue atteinte ; d’autres résultats peuvent exister."))
    return tuple(lines)


def public_rows(report):
    lines = [
        ("Portée", "Comparaison documentaire uniquement ; aucune origine ni droit attestés."),
        ("Couverture", "Partielle ; réponse éventuellement en cache, fraîcheur réseau non vérifiée."),
    ]
    for hit in report.matches:
        lines.append((hit.title, " · ".join(x for x in (
            hit.author, hit.provider_id, hit.record_id, ", ".join(hit.isbns),
            _LABELS.get(hit.comparison, "Comparaison descriptive"),
        ) if x)))
    if not report.matches:
        lines.append(("Résultat", "Aucune correspondance reçue ; absence non prouvée."))
    return tuple(lines)


def private_rows(report):
    lines = [
        ("Portée", "Compte sélectionné : " + report.provider_id + " · "
         + report.credential_id + " · " + (report.account_id or "principal")),
        ("Surface", "Achats connus" if report.kind == "purchase" else "Prêts connus"),
        ("Couverture", "Fenêtre partielle en cache. Ni recherche réseau ni origine ou droit attestés."),
    ]
    for hit in report.matches:
        lines.append((hit.title, " · ".join(x for x in (
            hit.author, hit.edition_id,
            _LABELS.get(hit.comparison, "Comparaison descriptive"),
            "Description d’achat en cache" if hit.evidence == "cached_public_purchase_description"
            else "Description de prêt en cache ; date inconnue",
        ) if x)))
    if report.window_truncated:
        lines.append(("Limite", "Fenêtre limitée ; d’autres correspondances peuvent exister."))
    if not report.matches:
        lines.append(("Résultat", "Aucune correspondance connue ; absence non prouvée."))
    return tuple(lines)


class DetailController:
    def __init__(self, broker, host, active):
        self.broker, self.host, self.active = broker, host, active
        self.identity, self.placement = host.identity_id, host.placement_id
        self.page = None
        self.used = set()
        self.busy = self.finished = self.disposed = False

    def current(self):
        return (not self.disposed and self.active() is True
                and self.host.is_current() is True
                and self.host.identity_id == self.identity
                and self.host.placement_id == self.placement)

    def mount(self):
        if self.current():
            self.host.add_action(
                "preview", "Suggestions de provenance",
                "Comparer le fichier aux notices publiques en cache, sans réseau ni attribution d’origine.",
                self.preview,
            )
        return self.dispose

    def preview(self):
        if self.busy or self.finished or not self.current():
            return False
        previous = self.page
        if previous is None:
            work = lambda: self.broker.preview(identity_id=self.identity,
                placement_id=self.placement, still_current=self.current, confirmed=True)
        elif previous.can_continue:
            work = lambda: self.broker.next_page(token=previous.token, confirmed=True)
        else:
            return False
        # First-page catalogue tokens expire as soon as pagination is requested.
        self.page = None
        self.used.clear()

        def display(value):
            if (type(value) is not LocalOriginDetailPage
                    or any(flag is not False for flag in (
                        value.coverage_complete, value.provider_access_performed,
                        value.association_authorized))):
                self.finished = previous is not None
                self.host.toast("Revue indisponible ; aucune absence attestée.")
                return
            self.page = value
            self.finished = not value.can_continue
            self.host.add_group("Suggestions de provenance — lecture seule", known_rows(value))
            if value.page == 1 and value.metadata_evidence == "verified_epub_opf_description":
                public_choices = value.public_choices[:_MAX_CATALOGUE_ACTIONS]
                for index, choice in enumerate(public_choices):
                    if not self.current():
                        return
                    self.host.add_action(
                        "public-" + str(index), "Envoyer le titre à " + choice.provider_id,
                        "Recherche publique explicite : seul le titre OPF est transmis. Ni compte privé, ni téléchargement.",
                        lambda selected=choice, original=value: self.search(selected, original),
                    )

                remaining = _MAX_CATALOGUE_ACTIONS - len(public_choices)
                private_choices = value.private_choices[:remaining]
                accounts = []
                for index, choice in enumerate(private_choices):
                    if not self.current():
                        return
                    surface = "achats" if choice.kind == "purchase" else "prêts"
                    # The short numbered action is backed by an exact,
                    # visible account description and full tooltip. Long
                    # coordinate IDs cannot cause the host to drop a button.
                    label = f"Comparer {surface} — compte {index + 1}"
                    coordinates = ("Fournisseur : " + choice.provider_id
                                   + " · Connexion : " + choice.credential_id
                                   + " · Compte : " + (choice.account_id or "principal"))
                    accounts.append((label, coordinates))
                    self.host.add_action(
                        "private-" + str(index), label,
                        "Cache local, aucune transmission. " + coordinates,
                        lambda selected=choice, original=value: self.compare_private(selected, original),
                    )
                if value.private_choices:
                    accounts.append(("Couverture",
                        "Choix partiels ; d’autres comptes ou surfaces peuvent exister."))
                hidden = (len(value.public_choices) - len(public_choices)
                          + len(value.private_choices) - len(private_choices))
                if hidden:
                    accounts.append(("Limite de la fiche",
                        f"{hidden} choix supplémentaires connus ne sont pas affichés "
                        "dans cette fenêtre. Aucune absence attestée."))
                if accounts:
                    self.host.add_group("Catalogues à comparer explicitement", tuple(accounts))

        return self._submit(work, display)

    def compare_private(self, choice, original):
        if (self.busy or not self.current() or self.page is not original
                or not any(item is choice for item in original.private_choices)
                or choice.token in self.used):
            return False
        self.used.add(choice.token)

        def display(value):
            if (type(value) is not LocalOriginPrivateReport or value.status != "partial"
                    or (value.provider_id, value.credential_id, value.account_id, value.kind)
                    != (choice.provider_id, choice.credential_id, choice.account_id, choice.kind)
                    or any(flag is not False for flag in (
                        value.coverage_complete, value.fresh_network_verified,
                        value.provider_access_performed, value.association_authorized))):
                self.host.toast("Comparaison privée indisponible ; aucune absence attestée.")
                return
            self.host.add_group("Catalogue privé — lecture seule", private_rows(value))

        return self._submit(
            lambda: self.broker.compare_private(token=choice.token, confirmed=True), display)

    def search(self, choice, original):
        if (self.busy or not self.current() or self.page is not original
                or not any(item is choice for item in original.public_choices)
                or choice.token in self.used):
            return False
        self.used.add(choice.token)

        def display(value):
            if (type(value) is not LocalOriginPublicReport or value.status != "partial"
                    or value.provider_id != choice.provider_id
                    or any(flag is not False for flag in (
                        value.coverage_complete, value.fresh_network_verified,
                        value.association_authorized))):
                self.host.toast("Comparaison publique indisponible ; aucune absence attestée.")
                return
            self.host.add_group("Catalogue public — " + choice.provider_id, public_rows(value))

        return self._submit(
            lambda: self.broker.search_public(token=choice.token, confirmed=True), display)

    def _submit(self, work, display):
        self.busy = True
        delivered = False

        def guarded():
            if not self.current():
                return None
            result = work()
            return result if self.current() else None

        def done(result):
            nonlocal delivered
            if delivered:
                return
            delivered = True
            self.busy = False
            if self.current():
                display(result)

        try:
            accepted = self.host.submit("Comparaison documentaire…", guarded, done)
        except Exception:
            accepted = False
        if accepted is not True:
            done(None)
        return accepted is True

    def dispose(self):
        self.disposed = True
        self.page = None
        self.used.clear()
