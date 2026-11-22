"""#7092 contextual provenance presentation, without GTK or private Core imports."""
from datetime import datetime, timezone
from unicodedata import category
from heapq import nsmallest

from cifereca.plugin_api.local_origin_detail import (
    LocalOriginDetailPage, LocalOriginPublicReport, LocalOriginPrivateReport,
    LocalOriginRemoteLoanReport, LocalOriginDeclarationAdvice,
    LocalOriginPurchaseDeclarationAdvice,
)

# The generic book-review host accepts 12 actions including preview.
# Keep the same limit locally and report any truncated choice window.
_MAX_CATALOGUE_ACTIONS = 11

_LABELS = {
    "title_related": "Titre voisin",
    "purchase_description_conflicting": "Des descriptions de cet achat divergent ; aucune valeur arbitrée",
    "title_concordant_author_unknown": "Titre concordant, auteur inconnu",
    "title_concordant_author_divergent": "Titre concordant, auteur divergent",
    "title_author_concordant": "Titre et auteur concordants",
    "isbn_title_author_concordant": "ISBN déclaré, titre et auteur concordants",
    "isbn_claim_ambiguous": "ISBN déclaré ambigu",
    "isbn_claim_divergent": "ISBN déclarés divergents",
    "title_concordant_author_unverified": "Titre concordant, auteur non vérifié",
}


_DOCUMENTARY_STRENGTH = {
    "isbn_title_author_concordant": 0,
    "title_author_concordant": 1,
    # An ISBN claimed by multiple editions is not an exact edition source.
    "isbn_claim_ambiguous": 2,
    "title_concordant_author_unknown": 3,
    "title_concordant_author_unverified": 3,
    "title_related": 4,
    "title_concordant_author_divergent": 5,
    "isbn_claim_divergent": 6,
    "purchase_description_conflicting": 7,
}
_SURFACE_PRIORITY = {
    "known-c": 0, "public": 0, "purchase": 1, "loan": 2, "remote-loan": 2,
}
_SURFACE_LABEL = {
    "known-c": "Notice C déjà connue",
    "public": "Catalogue public",
    "purchase": "Achat en cache",
    "loan": "Prêt en cache",
    "remote-loan": "Inventaire de prêts relu",
}


def _displayable_c_docbase(value):
    # C source coordinates are documentary hints, not a trusted UI channel.
    return (type(value) is str and 0 < len(value) <= 512
            and not any(category(ch) in ("Cc", "Cf", "Cs", "Zl", "Zp")
                        for ch in value))


def ranked_documentary_rows(compared):
    """Top ten from explicit review windows, bounded independently of pages.

    Evidence precedes catalogue family; ties preserve observation order.
    This projection creates neither a right, a provider source nor a new
    request. Only the displayed top ten need remain in working memory.
    """
    total = 0

    def candidates():
        nonlocal total
        for family, report in compared:
            if family not in _SURFACE_PRIORITY:
                continue
            for match in report.matches:
                total += 1
                strength = _DOCUMENTARY_STRENGTH.get(match.comparison, 8)
                provider = (match.provider_id if family in ("known-c", "public")
                            else report.provider_id)
                record = (match.record_id if family == "public" else
                          match.edition_id if family in ("known-c", "purchase", "loan",
                                                         "remote-loan") else "")
                yield (
                    strength, _SURFACE_PRIORITY[family],
                    match.title,
                    _SURFACE_LABEL[family] + " · " + provider
                    + (" · édition/référence : " + record if record else "")
                    + (" · Collection catalogue : " + match.docbase
                       + " · Support catalogue : " + match.medium
                       + (" · Format catalogue : " + match.media_format
                          if match.media_format else "")
                       if family == "public" else "")
                    + (" · Collection C : " + match.docbase
                       if family == "known-c" and _displayable_c_docbase(match.docbase)
                       else "")
                    + " · " + _LABELS.get(match.comparison, "Comparaison descriptive"),
                )

    # nsmallest maintains input order for equal (evidence, family) keys.
    # Unlike sorting every retained page this stores only ten comparisons.
    strongest = nsmallest(10, candidates(), key=lambda row: row[:2])
    if not strongest:
        return ()
    rows = [
        ("Portée", "Fenêtres explicitement comparées seulement ; tri documentaire, "
         "jamais attribution de provenance, d'achat, de prêt ou de droit."),
        ("Ordre", "Concordance d'abord ; à preuve égale, public, achats puis prêts. "
         "Un reçu fournisseur attesté n'est pas une simple suggestion."),
        ("Couverture", "Partielle, sans fraîcheur réseau ni inventaire exhaustif attestés."),
    ]
    rows.extend((title, description) for _, _, title, description in strongest)
    if total > len(strongest):
        rows.append(("Limite", f"{total - len(strongest)} candidats examinés de plus, "
                     "non affichés dans cet aperçu classé ; aucune absence attestée."))
    return tuple(rows)

def known_rows(page):
    lines = [
        ("Portée", "Notices publiques connues uniquement. Aucune origine ni droit attestés."),
        ("Couverture", "Partielle ; résultats relus indépendamment et susceptibles d’évoluer."),
        ("Page", str(page.page)),
        ("Résultats connus", str(page.known_matches)),
    ]
    for hit in page.matches:
        observed = "Dernière observation C : date indisponible"
        if type(hit.observed_at) is int and 0 <= hit.observed_at <= 253402300799:
            try:
                observed = ("Dernière observation C (UTC) : "
                            + datetime.fromtimestamp(hit.observed_at, tz=timezone.utc)
                            .strftime("%Y-%m-%d %H:%M"))
            except (OverflowError, OSError, ValueError):
                pass
        lines.append((hit.title, " · ".join(x for x in (
            hit.author, hit.provider_id,
            "Référence C : " + hit.record_id,
            "Collection C : " + hit.docbase if _displayable_c_docbase(hit.docbase) else "",
            "Édition C : " + hit.edition_id,
            _LABELS.get(hit.comparison, "Comparaison descriptive"),
            "Notice ancienne" if hit.stale else "Notice en cache",
            observed,
        ) if x)))
    if not page.matches:
        verdict = ("Correspondances C connues, aucune fiche présentable sur cette page ; "
                   "absence non prouvée." if page.known_matches > 0 else
                   "Aucune correspondance connue ; absence non prouvée.")
        lines.append(("Résultat", verdict))
    if page.has_more and not page.can_continue:
        lines.append(("Limite", "Limite de revue atteinte ; d’autres résultats peuvent exister."))
    return tuple(lines)


def public_rows(report):
    lines = [
        ("Portée", "Comparaison documentaire uniquement ; aucune origine ni droit attestés."),
        ("Couverture", "Partielle ; réponse éventuellement en cache, fraîcheur réseau non vérifiée."),
    ]
    for hit in report.matches:
        # Every displayed coordinate comes from this one explicit catalogue
        # response, not from the local EPUB, an attested right or a C write.
        lines.append((hit.title, " · ".join(x for x in (
            hit.author, hit.provider_id,
            "Référence catalogue : " + hit.record_id,
            "Collection catalogue : " + hit.docbase,
            "Support catalogue : " + hit.medium,
            "Format catalogue : " + hit.media_format if hit.media_format else "",
            "ISBN déclarés par le catalogue : " + ", ".join(hit.isbns) if hit.isbns else "",
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
        ("Page", str(getattr(report, "page", 1))),
        ("Couverture", "Fenêtre partielle en cache. Ni recherche réseau ni origine ou droit attestés."),
    ]
    for hit in report.matches:
        observed = "Date de description inconnue"
        if type(hit.observed_at) is int and 0 < hit.observed_at <= 253402300799:
            try:
                observed = "Description observée le " + datetime.fromtimestamp(
                    hit.observed_at, tz=timezone.utc,
                ).strftime("%Y-%m-%d %H:%M UTC")
            except (OverflowError, ValueError, OSError):
                pass
        lines.append((hit.title, " · ".join(x for x in (
            hit.author, "Édition : " + hit.edition_id,
            "Branche : " + hit.branch if hit.branch else "Branche non renseignée",
            "Occurrence : " + hit.resource_id if hit.resource_id else "Occurrence non renseignée",
            _LABELS.get(hit.comparison, "Comparaison descriptive"),
            "Description d’achat en cache" if hit.evidence == "cached_public_purchase_description"
            else "Description de prêt en cache",
            observed,
        ) if x)))
    if report.window_truncated:
        lines.append(("Limite", "Fenêtre limitée ; d’autres correspondances peuvent exister."))
    if not report.matches:
        lines.append(("Résultat", "Aucune correspondance connue ; absence non prouvée."))
    return tuple(lines)


def remote_loan_rows(report):
    lines = [
        ("Portée", "Compte sélectionné : " + report.provider_id + " · "
         + report.credential_id + " · " + (report.account_id or "principal")),
        ("Surface", "Inventaire de prêts explicitement relu ; titre et fichier locaux non transmis."),
        ("Couverture", "Partielle ; fraîcheur réseau non vérifiée. Aucune origine ni droit attestés."),
        ("Date de l’inventaire", report.observed_at or "Non renseignée"),
    ]
    for hit in report.matches:
        lines.append((hit.title, " · ".join(x for x in (
            hit.author, "Édition : " + hit.edition_id if hit.edition_id else "Édition non renseignée",
            "Occurrence : " + hit.loan_id,
            "Branche canonique non attestée",
            _LABELS.get(hit.comparison, "Comparaison descriptive"),
        ) if x)))
    if report.window_truncated:
        lines.append(("Limite", "Résultats limités ; d’autres correspondances peuvent exister."))
    if not report.matches:
        lines.append(("Résultat", "Aucune correspondance reçue ; absence non prouvée."))
    return tuple(lines)


class DetailController:
    def __init__(self, broker, host, active):
        self.broker, self.host, self.active = broker, host, active
        self.identity, self.placement = host.identity_id, host.placement_id
        self.page = None
        self.used = set()
        self.choice_window = ()
        self.choice_actions = ()
        self.choice_offset = None
        self.private_next = {}
        self.declaration_view = None
        self.compared = []
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
        self.private_next.clear()
        self.declaration_view = None
        self.compared.clear()

        def display(value):
            if previous is not None and callable(getattr(self.host, "retire_action", None)):
                for key in self.choice_actions:
                    self.host.retire_action(key)
                self.choice_actions = ()
                self.choice_window = ()
                self.choice_offset = None
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
            if value.matches:
                self.compared.append(("known-c", value))
            if value.page == 1 and value.metadata_evidence == "verified_epub_opf_description":
                if callable(getattr(self.host, "retire_action", None)):
                    self._show_choice_window(value, 0)
                    return
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
                remote_choices = value.remote_loan_choices[:remaining - len(private_choices)]
                for index, choice in enumerate(remote_choices):
                    if not self.current():
                        return
                    label = f"Relire les prêts — compte {index + 1}"
                    coordinates = ("Fournisseur : " + choice.provider_id
                                   + " · Connexion : " + choice.credential_id
                                   + " · Compte : " + (choice.account_id or "principal"))
                    accounts.append((label, coordinates))
                    self.host.add_action(
                        "remote-loan-" + str(index), label,
                        "Lecture du compte auprès du fournisseur ; aucun titre local transmis. " + coordinates,
                        lambda selected=choice, original=value: self.compare_remote_loan(selected, original),
                    )
                if value.private_choices or value.remote_loan_choices:
                    accounts.append(("Couverture",
                        "Choix partiels ; d’autres comptes ou surfaces peuvent exister."))
                hidden = (len(value.public_choices) - len(public_choices)
                          + len(value.private_choices) - len(private_choices)
                          + len(value.remote_loan_choices) - len(remote_choices))
                if hidden:
                    accounts.append(("Limite de la fiche",
                        f"{hidden} choix supplémentaires connus ne sont pas affichés "
                        "dans cette fenêtre. Aucune absence attestée."))
                if accounts:
                    self.host.add_group("Catalogues à comparer explicitement", tuple(accounts))

        return self._submit(work, display)

    def _show_choice_window(self, original, offset):
        """Page inert, already admitted choices without an extra provider query."""
        if not self.current() or self.page is not original:
            return
        for key in self.choice_actions:
            if self.host.retire_action(key) is not True:
                self.choice_window = ()
                return
        self.choice_actions = ()
        self.choice_window = ()
        self.private_next.clear()
        self.declaration_view = None
        choices = (tuple(("public", x) for x in original.public_choices)
                   + tuple(("private", x) for x in original.private_choices)
                   + tuple(("remote-loan", x) for x in original.remote_loan_choices))
        # Ten choices + an optional continuation + the permanent preview.
        count = 10 if len(choices) - offset > 11 else 11
        window = choices[offset:offset + count]
        self.choice_window = tuple(choice for _, choice in window)
        self.choice_offset = offset + len(window)
        keys, accounts = [], []
        for index, (kind, choice) in enumerate(window, offset + 1):
            if not self.current():
                return
            key = ("public-" + str(index - 1) if kind == "public"
                   else "private-" + str(index - 1 - len(original.public_choices))
                   if kind == "private" else "remote-loan-" + str(
                       index - 1 - len(original.public_choices) - len(original.private_choices)))
            if kind == "public":
                title = "Envoyer le titre à " + choice.provider_id
                tooltip = "Recherche publique explicite : seul le titre OPF est transmis. Ni compte privé, ni téléchargement."
                action = lambda selected=choice: self.search(selected, original)
            elif kind == "private":
                surface = "achats" if choice.kind == "purchase" else "prêts"
                title = f"Comparer {surface} — compte {index - len(original.public_choices)}"
                coordinates = ("Fournisseur : " + choice.provider_id
                               + " · Connexion : " + choice.credential_id
                               + " · Compte : " + (choice.account_id or "principal"))
                tooltip = "Cache local, aucune transmission. " + coordinates
                accounts.append((title, coordinates))
                action = lambda selected=choice: self.compare_private(selected, original)
            else:
                ordinal = index - len(original.public_choices) - len(original.private_choices)
                title = f"Relire les prêts — compte {ordinal}"
                coordinates = ("Fournisseur : " + choice.provider_id
                               + " · Connexion : " + choice.credential_id
                               + " · Compte : " + (choice.account_id or "principal"))
                tooltip = "Lecture du compte auprès du fournisseur ; aucun titre local transmis. " + coordinates
                accounts.append((title, coordinates))
                action = lambda selected=choice: self.compare_remote_loan(selected, original)
            self.host.add_action(key, title, tooltip, action)
            keys.append(key)
        following = offset + len(window)
        if following < len(choices):
            key = "catalogue-more-" + str(following)
            self.host.add_action(
                key, "Autres catalogues", "Afficher les choix suivants sans recherche.",
                lambda: self._next_choice_window(original, following),
            )
            keys.append(key)
        self.choice_actions = tuple(keys)
        if accounts or len(choices) > 11:
            accounts.append(("Fenêtre", f"Choix {offset + 1} à {following} sur {len(choices)} connus. Découverte partielle ; aucune absence attestée."))
        if accounts:
            self.host.add_group("Catalogues à comparer explicitement", tuple(accounts))

    def _next_choice_window(self, original, offset):
        if (self.busy or not self.current() or self.page is not original
                or self.choice_offset != offset):
            return False
        return self._submit(
            lambda: original,
            lambda value: self._show_choice_window(original, offset)
            if value is original else None,
        )

    def _private_current(self, choice, original):
        return (not self.busy and self.current() and self.page is original
                and any(item is choice for item in original.private_choices)
                and (not callable(getattr(self.host, "retire_action", None))
                     or any(item is choice for item in self.choice_window)))

    def _private_action_key(self, choice, original):
        return next("private-" + str(index)
                    for index, item in enumerate(original.private_choices)
                    if item is choice)

    def _retire_private(self, choice, original):
        key = self._private_action_key(choice, original)
        retire = getattr(self.host, "retire_action", None)
        if key in self.choice_actions and callable(retire) and retire(key) is True:
            self.choice_actions = tuple(item for item in self.choice_actions if item != key)
            return True
        return False

    def compare_private(self, choice, original):
        if not self._private_current(choice, original) or choice.token in self.used:
            return False
        return self._private_page(choice, original, choice.token, 1)

    def next_private(self, choice, original):
        if not self._private_current(choice, original):
            return False
        pending = self.private_next.pop(choice.token, None)
        if pending is None or pending[0] in self.used:
            return False
        token, page = pending
        return self._private_page(choice, original, token, page)

    def _private_page(self, choice, original, token, page):
        self.used.add(token)

        def display(value):
            if (type(value) is not LocalOriginPrivateReport or value.status != "partial"
                    or (value.provider_id, value.credential_id, value.account_id, value.kind)
                    != (choice.provider_id, choice.credential_id, choice.account_id, choice.kind)
                    or any(flag is not False for flag in (
                        value.coverage_complete, value.fresh_network_verified,
                        value.provider_access_performed, value.association_authorized))
                    or type(getattr(value, "page", 1)) is not int
                    or getattr(value, "page", 1) != page):
                self._retire_private(choice, original)
                self.host.toast("Comparaison privée indisponible ; aucune absence attestée.")
                return
            following = getattr(value, "next_token", None)
            if following is not None and (type(following) is not str
                    or len(following) != 64 or any(c not in "0123456789abcdef" for c in following)
                    or following in self.used or value.window_truncated is not True or page >= 32):
                self._retire_private(choice, original)
                self.host.toast("Continuation privée indisponible ; aucune absence attestée.")
                return
            self.host.add_group("Catalogue privé — lecture seule", private_rows(value))
            self._record_comparison(choice.kind, value, original)
            if (choice.kind in ("loan", "purchase")
                    and callable(getattr(self.host, "retire_action", None))
                    and callable(getattr(self.broker, "preview_" + choice.kind + "_declaration", None))
                    and (any(getattr(hit, "declaration_choice", None) for hit in value.matches)
                         or "declare-next-page" in self.choice_actions)):
                self._show_declaration_window(original, choice, value, 0)
                return
            if following is None:
                if page > 1:
                    self._retire_private(choice, original)
                return
            if page == 1:
                # Replace this account's first action only once. Subsequent
                # clicks consume the next live token from a stable action slot,
                # even for a 32-page catalogue. No auto-draining of pages.
                if not self._retire_private(choice, original):
                    return
                key = self._private_action_key(choice, original)
                index = next(i for i, item in enumerate(original.private_choices, 1) if item is choice)
                surface = "achats" if choice.kind == "purchase" else "prêts"
                self.host.add_action(
                    key, f"Suite {surface} — compte {index}",
                    "Page suivante du même cache, sans transmission. Fournisseur : "
                    + choice.provider_id + " · Connexion : " + choice.credential_id
                    + " · Compte : " + (choice.account_id or "principal"),
                    lambda: self.next_private(choice, original),
                )
                self.choice_actions = (*self.choice_actions, key)
            self.private_next[choice.token] = (following, page + 1)

        return self._submit(
            lambda: self.broker.compare_private(token=token, confirmed=True), display)

    def _show_declaration_window(self, original, choice, report, offset):
        """Display the exact selections within the host's existing action budget.

        Each partial cache page can be examined. Its continuation is explicit
        and revokes the preceding page's candidate callbacks.
        """
        if not self.current() or self.page is not original:
            return
        self.declaration_view = None
        for key in self.choice_actions:
            if self.host.retire_action(key) is not True:
                return
        self.choice_actions = ()
        self.choice_window = ()
        self.choice_offset = None
        matches = tuple(hit for hit in report.matches
                        if type(hit.declaration_choice) is str
                        and len(hit.declaration_choice) == 64
                        and all(c in "0123456789abcdef" for c in hit.declaration_choice))
        window = matches[offset:offset + (8 if report.next_token is not None else 9)]
        lease = object()
        self.declaration_view = lease

        def current():
            return (not self.busy and self.current() and self.page is original
                    and self.declaration_view is lease)

        keys, rows = [], []
        for index, hit in enumerate(window, offset + 1):
            key = "declare-preview-" + str(index)
            label = f"Examiner la déclaration {index}"
            rows.append((label, "Édition : " + hit.edition_id + " · Branche : "
                         + (hit.branch or "non renseignée") + " · "
                         + ("Prêt" if choice.kind == "loan" else "Achat") + " : "
                         + (hit.resource_id or "non renseigné")))
            self.host.add_action(
                key, label, "Aperçu seulement, sans association ni droit créé.",
                lambda selected=hit: self._preview_declaration(
                    original, choice, selected, current))
            keys.append(key)

        def more():
            if not current():
                return False
            self.declaration_view = None
            return self._submit(lambda: report,
                lambda value: self._show_declaration_window(
                    original, choice, report, offset + len(window)) if value is report else None)

        if offset + len(window) < len(matches):
            self.host.add_action("declare-more", "Autres occurrences",
                                 "Afficher les choix suivants sans requête.", more)
            keys.append("declare-more")

        if report.next_token is not None:
            def advance():
                if not current() or report.next_token in self.used:
                    return False
                self.declaration_view = None
                return self._private_page(choice, original, report.next_token, report.page + 1)
            self.host.add_action("declare-next-page", "Suite du cache privé",
                                 "Page suivante du même cache ; les anciens choix expirent.", advance)
            keys.append("declare-next-page")

        def back():
            if not current():
                return False
            self.declaration_view = None
            return self._submit(lambda: original,
                lambda value: self._show_choice_window(original, 0) if value is original else None)

        self.host.add_action("declare-back", "Revenir aux catalogues",
                             "Abandonner la sélection de déclaration.", back)
        keys.append("declare-back")
        self.choice_actions = tuple(keys)
        if rows:
            self.host.add_group("Occurrence à examiner", tuple(rows))

    def _preview_declaration(self, original, choice, selected, current):
        if (not current() or selected.declaration_choice in self.used
                or choice.kind not in ("loan", "purchase")):
            return False
        operation = getattr(self.broker, "preview_" + choice.kind + "_declaration", None)
        if not callable(operation):
            return False
        expected = (LocalOriginDeclarationAdvice if choice.kind == "loan"
                    else LocalOriginPurchaseDeclarationAdvice)
        resource_field = "loan_id" if choice.kind == "loan" else "purchase_id"
        resource_label = "Prêt" if choice.kind == "loan" else "Achat"
        token = selected.declaration_choice
        self.used.add(token)
        self.declaration_view = None

        def display(value):
            if (type(value) is not expected
                    or value.trust != "user_declaration"
                    or value.association_authorized is not False
                    or (value.provider_id, value.credential_id, value.account_id,
                        value.edition_id, getattr(value, resource_field)) != (
                            choice.provider_id, choice.credential_id, choice.account_id,
                            selected.edition_id, selected.resource_id)):
                self.host.toast("Déclaration indisponible ; aucune association créée.")
            else:
                self.host.add_group("Aperçu de déclaration utilisateur", (
                    ("Compte", value.provider_id + " · " + value.credential_id
                     + " · " + (value.account_id or "principal")),
                    ("Édition", value.edition_id), (resource_label, getattr(value, resource_field)),
                    ("Effet", "Déclaration utilisateur uniquement. Aucune origine attestée, "
                     "aucune modification de droit, de fichier ou de placement."),
                    ("Disponibilité", "Confirmation native distincte requise ; aucune écriture à l’aperçu."),
                ))
            self._show_choice_window(original, 0)

        return self._submit(
            lambda: operation(token=token, confirmed=True), display)

    def compare_remote_loan(self, choice, original):
        if (self.busy or not self.current() or self.page is not original
                or not any(item is choice for item in original.remote_loan_choices)
                or (callable(getattr(self.host, "retire_action", None))
                    and not any(item is choice for item in self.choice_window))
                or choice.token in self.used
                or not callable(getattr(self.broker, "compare_remote_loan", None))):
            return False
        self.used.add(choice.token)

        def display(value):
            if (type(value) is not LocalOriginRemoteLoanReport or value.status != "partial"
                    or (value.provider_id, value.credential_id, value.account_id)
                    != (choice.provider_id, choice.credential_id, choice.account_id)
                    or value.provider_read_requested is not True
                    or any(flag is not False for flag in (
                        value.coverage_complete, value.fresh_network_verified,
                        value.association_authorized, value.local_description_transmitted))):
                self.host.toast("Inventaire privé indisponible ; aucune absence attestée.")
                return
            self.host.add_group("Inventaire privé de prêts — lecture seule", remote_loan_rows(value))
            self._record_comparison("remote-loan", value, original)

        return self._submit(
            lambda: self.broker.compare_remote_loan(token=choice.token, confirmed=True), display)

    def search(self, choice, original):
        if (self.busy or not self.current() or self.page is not original
                or not any(item is choice for item in original.public_choices)
                or (callable(getattr(self.host, "retire_action", None))
                    and not any(item is choice for item in self.choice_window))
                or choice.token in self.used):
            return False
        self.used.add(choice.token)

        def display(value):
            if (type(value) is not LocalOriginPublicReport or value.status != "partial"
                    or value.provider_id != choice.provider_id
                    or any(flag is not False for flag in (
                        value.coverage_complete, value.fresh_network_verified,
                        value.association_authorized))):
                reason = ({
                        "auth_required": "Authentification requise",
                        "access_denied": "Accès refusé",
                        "rate_limited": "Limite de requêtes atteinte",
                        "timeout": "Délai de réponse dépassé",
                        "cancelled": "Comparaison annulée",
                    }.get(value.status)
                          if type(value) is LocalOriginPublicReport
                          and value.provider_id == choice.provider_id else None)
                self.host.toast((reason or "Comparaison publique indisponible") +
                                " ; aucune absence attestée. Nouvelle revue nécessaire.")
                return
            self.host.add_group("Catalogue public — " + choice.provider_id, public_rows(value))
            self._record_comparison("public", value, original)

        return self._submit(
            lambda: self.broker.search_public(token=choice.token, confirmed=True), display)

    def _record_comparison(self, family, report, original):
        if not self.current() or self.page is not original or not report.matches:
            return
        # Local, per-view presentation only; no provider request or retained
        # source cache. The host appends immutable snapshot groups.
        self.compared.append((family, report))
        if len(self.compared) > 1:
            rows = ranked_documentary_rows(self.compared)
            if rows:
                self.host.add_group("Classement documentaire — lecture seule", rows)

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
        self.compared.clear()
        self.used.clear()
        self.private_next.clear()
        self.declaration_view = None
