"""Paris catalogue operations; the owning client retains transport and session state."""
from __future__ import annotations

from dataclasses import replace
from typing import Any
from urllib.parse import quote
import uuid
import httpx
from cifereca.plugin_api import SearchResult, ItemDetail
from cifereca_paris_constants import SEARCH_PATH, HOLDINGS_PATH
from cifereca_paris_normalize import normalize_search_result, normalize_item_detail
from cifereca_paris_bibliography import project_search_response_facets


class ParisCatalogueOperations:
    def _search_query(self, query: str, scenario: str, page: int, result_size: int = 50) -> dict[str, Any]:
        encoded = quote(query)
        return {
            "Page": page, "ResultSize": result_size, "QueryString": query, "SearchLabel": "",
            "ScenarioCode": scenario, "InitialSearch": True, "SearchContext": 0, "PageRange": 3,
            "Url": f"{self.base_url}/search.aspx?SC={scenario}&QUERY={encoded}&QUERY_LABEL=#/Search/(query:(InitialSearch:!t,Page:{page},PageRange:3,QueryString:'{encoded}',ResultSize:{result_size},ScenarioCode:{scenario},SearchContext:0,SearchLabel:''))",
        }

    def _search_scenario(
        self, query: str, scenario: str, page: int, result_size: int,
    ) -> tuple[list[SearchResult], list[dict[str, Any]], bool]:
        from cifereca_paris_client import ParisSearchResponseInvalid
        body = {"query": self._search_query(query, scenario, page, result_size), "sst": 4}
        r = self.client.post(
            f"{self.base_url}{SEARCH_PATH}",
            headers={**self._api_headers(), "Content-Type": "application/json; charset=UTF-8", "Origin": self.base_url, "Referer": f"{self.base_url}/search.aspx?SC={scenario}"},
            json=body,
        )
        # SearchService's 401/403 is an access verdict, not a wide-query
        # result-size problem. 408/429/5xx are HTTP outages: a smaller POST
        # to the same route does not repair them. Keep business responses
        # (including the documented broad-query error) on the existing
        # controlled 50 -> 20 retry path.
        if r.status_code in {401, 403, 408, 429} or r.status_code >= 500:
            r.raise_for_status()
        data = self._json(r, f"search {scenario}")
        envelope = data.get("d")
        rows = envelope.get("Results") if type(envelope) is dict else None
        # Only an explicit native array can attest this page, including zero.
        # A missing/null/malformed collection cannot certify an empty search.
        if type(rows) is not list:
            raise ParisSearchResponseInvalid("SearchService.d.Results: tableau absent ou invalide")
        results = []
        for index, raw in enumerate(rows):
            resource = raw.get("Resource") if type(raw) is dict else None
            if (type(resource) is not dict
                    or type(resource.get("RscId")) not in (str, int)
                    or type(resource.get("RscBase")) is not str
                    or not str(resource["RscId"]).strip()
                    or not resource["RscBase"].strip()):
                continue
            try:
                item = normalize_search_result(raw, scenario)
            except (ValueError, TypeError, AttributeError, KeyError, OverflowError) as exc:
                self.diagnostics.add(
                    "CATALOG", f"search {scenario} parse result", "error",
                    detail={"index": index, "error": str(exc), "http_status": r.status_code},
                )
                continue
            if item is not None:
                results.append(item)
        if rows and not results:
            raise ParisSearchResponseInvalid("SearchService.d.Results: aucun résultat décodable")
        partial = len(results) != len(rows)
        if partial:
            self.diagnostics.add(
                "CATALOG", f"search {scenario} parse page", "partial",
                detail={"received": len(rows), "decoded": len(results),
                        "http_status": r.status_code},
            )
        return results, project_search_response_facets(data, scenario, page), partial

    def search_catalog(self, query: str, scenario: str = "CATALOGUE", page: int = 0, result_size: int = 20) -> list[SearchResult]:
        """Recherche un seul catalogue sans modifier l'état de recherche UI.

        Utilisé notamment pour enrichir à la demande les couvertures d'historique
        d'un profil lié. La méthode reste bornée afin de ne jamais transformer ce
        fallback en moissonnage du catalogue.
        """
        scenario = str(scenario or "CATALOGUE").upper()
        if scenario not in {"CATALOGUE", "EBOOKS"}:
            raise ValueError(f"Scénario de recherche inconnu: {scenario}")
        size = max(1, min(int(result_size), 50))
        return self._timed(
            "CATALOG", f"history cover search {scenario}",
            lambda: self._search_scenario(query, scenario, page, size)[0],
        )

    def search(self, query: str, scope: str = "all", page: int = 0) -> list[SearchResult]:
        rows, _facets, _warnings = self.search_response(query, scope, page)
        return rows

    def search_response(
        self, query: str, scope: str = "all", page: int = 0,
    ) -> tuple[list[SearchResult], list[dict[str, Any]], list[str]]:
        """Recherche tolérante aux défaillances d'un des deux catalogues.

        Les requêtes très larges pouvaient demander ``ResultSize=-1`` et provoquer
        l'erreur métier SearchService « Votre recherche a échoué ». On borne
        désormais la page à 50 résultats/scénario, avec un retry plus conservateur
        à 20 résultats. En mode ``all``, une panne EBOOKS ne masque jamais les
        résultats physiques déjà récupérés.
        """
        from cifereca_paris_client import ParisError, ParisSearchResponseInvalid, ParisSearchUnavailable
        scenarios = ["EBOOKS"] if scope == "digital" else ["CATALOGUE"] if scope == "physical" else ["CATALOGUE", "EBOOKS"]
        out: list[SearchResult] = []
        positions: dict[str, int] = {}
        self.last_search_warnings = []
        self.last_search_response_facets = []
        warnings: list[str] = []
        facets: list[dict[str, Any]] = []
        failures: list[tuple[str, Exception]] = []
        successful_scenarios = 0

        for scenario in scenarios:
            try:
                rows, branch_facets, branch_partial = self._timed(
                    "CATALOG", f"search {scenario}",
                    lambda scenario=scenario: self._search_scenario(query, scenario, page, 50),
                )
            except ParisSearchResponseInvalid as exc:
                # A smaller query cannot repair an invalid response contract.
                failures.append((scenario, exc))
                caption = "Catalogue numérique" if scenario == "EBOOKS" else "Catalogue physique"
                warnings.append(caption + " : réponse invalide")
                continue
            except ParisError as first_exc:
                # Retry borné : utile surtout pour EBOOKS sur les recherches larges.
                self.diagnostics.add(
                    "CATALOG", f"search {scenario} fallback", "retry",
                    detail={"result_size": 20, "error": type(first_exc).__name__},
                    level="detailed",
                )
                try:
                    rows, branch_facets, branch_partial = self._timed(
                        "CATALOG", f"search {scenario} fallback",
                        lambda scenario=scenario: self._search_scenario(query, scenario, page, 20),
                    )
                except Exception as second_exc:
                    failures.append((scenario, second_exc))
                    caption = "Catalogue numérique" if scenario == "EBOOKS" else "Catalogue physique"
                    warnings.append(
                        caption + (" : réponse invalide" if isinstance(
                            second_exc, ParisSearchResponseInvalid,
                        ) else " momentanément indisponible")
                    )
                    continue
            except httpx.HTTPError as exc:
                # Transport/auth/status failures do not need a same-endpoint
                # re-POST with smaller result size. Keep the branch independent.
                failures.append((scenario, exc))
                caption = "Catalogue numérique" if scenario == "EBOOKS" else "Catalogue physique"
                status = (
                    exc.response.status_code
                    if isinstance(exc, httpx.HTTPStatusError) else None
                )
                if status == 401:
                    warning = caption + " : authentification requise"
                elif status == 403:
                    warning = caption + " : accès refusé"
                else:
                    warning = caption + " momentanément indisponible"
                warnings.append(warning)
                continue

            facets.extend(branch_facets)
            if branch_partial:
                caption = "Catalogue numérique" if scenario == "EBOOKS" else "Catalogue physique"
                warnings.append(caption + " : résultats partiellement décodés")
            # An empty result from an available catalogue is still a
            # successful search, not evidence that every route failed.
            successful_scenarios += 1
            for item in rows:
                # Scenario is a search *route*, not positive per-item medium.
                # A CATALOGUE page can contain DILICOM ebooks; an EBOOKS
                # page can also contain a physical Syracuse record.
                if scope in {"physical", "digital"} and item.medium != scope:
                    continue
                initial = dict(item.metadata or {})
                provenances = initial.get("search_scenarios")
                if type(provenances) not in (tuple, list):
                    provenances = ()
                provenances = tuple(
                    name for name in provenances
                    if type(name) is str and name in {"CATALOGUE", "EBOOKS"}
                )
                if scenario not in provenances:
                    provenances += (scenario,)
                initial["search_scenarios"] = list(dict.fromkeys(provenances))
                if item.id not in positions:
                    positions[item.id] = len(out)
                    out.append(replace(item, metadata=initial))
                    continue
                # One exact record can be seen through both search routes.
                # Keep *both* sightings rather than attributing ownership,
                # file receipt or publication to the first successful route.
                pos = positions[item.id]
                previous = out[pos]
                metadata = dict(previous.metadata or {})
                paths = metadata.get("search_scenarios")
                paths = list(paths) if type(paths) in (list, tuple) else []
                metadata["search_scenarios"] = list(dict.fromkeys(
                    [name for name in paths + list(provenances)
                     if type(name) is str and name in {"CATALOGUE", "EBOOKS"}]
                ))
                # Contradictory factual classifications of the SAME ID
                # must not silently choose one as proof of physical/digital.
                def documentary_facts(row):
                    # Provider wording, capitalization and raw source-code
                    # provenance are not classification *differences*.
                    # Compare the four closed attested axes, never raw_code.
                    from cifereca.plugin_api import DocumentClassification
                    proof = row.document_classification
                    if type(proof) is not DocumentClassification:
                        return None
                    return (
                        tuple(
                            (getattr(proof, axis).status, getattr(proof, axis).value)
                            for axis in (
                                "medium", "document_kind",
                                "material_format", "digital_format",
                            )
                        ),
                        proof.offered_file_formats,
                    )

                same_classification = (
                    documentary_facts(previous) == documentary_facts(item)
                    and previous.medium == item.medium
                )
                # A duplicate exact record may carry different result-owned
                # series in the two Search routes. Do not let first-route
                # order establish a public relation or restore a relation
                # already refused by an earlier duplicate.
                from cifereca_paris_bibliography import public_search_series_metadata
                first_series = public_search_series_metadata(previous.metadata, previous.scenario)
                next_series = public_search_series_metadata(item.metadata, item.scenario)
                if (not same_classification or not first_series or not next_series
                        or first_series["series"] != next_series["series"]):
                    metadata.pop("series", None)
                    claims = metadata.get("bibliography_evidence")
                    if type(claims) is dict and "series" in claims:
                        remaining = {key: value for key, value in claims.items()
                                     if key != "series"}
                        if remaining:
                            metadata["bibliography_evidence"] = remaining
                        else:
                            metadata.pop("bibliography_evidence", None)
                # Resource.Subj is independent of the page's Subj facet.
                # Do not let route order select conflicting headings or
                # restore evidence already refused on a previous duplicate.
                from cifereca_paris_bibliography import public_search_subject_metadata
                first_subjects = public_search_subject_metadata(previous.metadata, previous.scenario)
                next_subjects = public_search_subject_metadata(item.metadata, item.scenario)
                if (not same_classification or not first_subjects or not next_subjects
                        or set(first_subjects["subjects"]) != set(next_subjects["subjects"])):
                    claims = metadata.get("bibliography_evidence")
                    if type(claims) is dict and "subjects" in claims:
                        remaining = {key: value for key, value in claims.items()
                                     if key != "subjects"}
                        if remaining:
                            metadata["bibliography_evidence"] = remaining
                        else:
                            metadata.pop("bibliography_evidence", None)
                    # Legacy result-owned display may still show the first
                    # route's headings, but cannot declare them in public C.
                if same_classification:
                    out[pos] = replace(previous, metadata=metadata)
                else:
                    from cifereca.plugin_api import DocumentClassification
                    metadata["document_kind"] = "unknown"
                    metadata["document_medium"] = "unknown"
                    out[pos] = replace(
                        previous, medium="unknown", content_type="unknown",
                        document_classification=DocumentClassification(),
                        metadata=metadata,
                    )

        if warnings and successful_scenarios:
            warnings = ["partial_results : " + warning for warning in warnings]
        # Preserve the last-call diagnostics for compatibility. The return
        # value uses the local collector even if another call completes now.
        self.last_search_warnings = warnings
        self.last_search_response_facets = facets
        if failures and not successful_scenarios:
            # No public stale fallback on a verified 401/403, even when
            # another branch had a timeout. Inspect httpx's numeric response
            # only, never exception text, request URL or private payload.
            http_statuses = [
                error.response.status_code for _name, error in failures
                if isinstance(error, httpx.HTTPStatusError)
            ]
            if 403 in http_statuses:
                error = ParisSearchUnavailable("Accès au catalogue Paris refusé.")
                error.code = "search_access_denied"
                error.retryable = False
            elif 401 in http_statuses:
                error = ParisSearchUnavailable("Authentification requise pour le catalogue Paris.")
                error.code = "search_authentication_required"
                error.retryable = False
            elif any(isinstance(failure, ParisSearchResponseInvalid)
                     for _name, failure in failures):
                error = ParisSearchUnavailable("Réponse SearchService Paris invalide.")
                error.code = "search_response_invalid"
                error.retryable = False
            else:
                message = (
                    "Le catalogue numérique n'a pas répondu à cette recherche. Essayez un terme plus précis ou réessayez dans quelques instants."
                    if scope == "digital" else
                    "Le catalogue physique n'a pas répondu à cette recherche. Réessayez dans quelques instants."
                    if scope == "physical" else
                    "Les catalogues Paris n'ont pas répondu à cette recherche. Réessayez dans quelques instants."
                )
                error = ParisSearchUnavailable(message)
            # Do not chain raw Syracuse errors (which can contain the
            # request URL, personal cookies, or provider response text).
            raise error
        return out, facets, warnings

    def _holdings_payload(self, docbase: str, record_id: str, scenario: str, search_terms: str = "") -> dict[str, Any]:
        """Return the raw GetListHoldings payload.

        The normalized holdings view intentionally discards HtmlView, but the
        official Paris PNB borrow flow publishes its signed entry point there.
        """
        query = {
            "FacetFilter": "{}", "ForceSearch": False, "InitialSearch": False, "Page": 0, "PageRange": 3,
            "QueryGuid": str(uuid.uuid4()), "QueryString": search_terms, "ResultSize": 50, "ScenarioCode": scenario,
            "ScenarioDisplayMode": "display-standard", "SearchGridFieldsShownOnResultsDTO": [], "SearchLabel": "",
            "SearchTerms": search_terms, "SortField": None, "SortOrder": 0,
            "TemplateParams": {"Scenario": "", "Scope": "Default", "Size": None, "Source": "", "Support": "", "UseCompact": False},
            "Url": f"{self.base_url}/search.aspx?SC={scenario}", "UseSpellChecking": None,
        }
        r = self.client.post(
            f"{self.base_url}{HOLDINGS_PATH}",
            headers={**self._api_headers(), "Content-Type": "application/json; charset=UTF-8", "Origin": self.base_url, "Referer": f"{self.base_url}/search.aspx?SC={scenario}"},
            json={"Record": {"RscId": record_id, "Docbase": docbase}, "searchQuery": query},
        )
        return self._json(r, "holdings")

    def holdings(self, docbase: str, record_id: str, scenario: str, search_terms: str = "") -> ItemDetail:
        def work():
            data = self._holdings_payload(docbase, record_id, scenario, search_terms)
            return normalize_item_detail(docbase, record_id, data)
        return self._timed("CATALOG", "holdings", work)

