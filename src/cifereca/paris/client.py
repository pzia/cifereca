from __future__ import annotations

import logging
import json
import time
import uuid
from importlib import metadata
from dataclasses import asdict
from typing import Any, Iterable, Callable
from urllib.parse import quote
import httpx
from ..diagnostics import Diagnostics
from ..models import Account, Booking, HistoryItem, ItemDetail, Loan, SearchResult
from .constants import ACCOUNT_SERVICE, BASE_URL, CHECK_AVAILABILITY_PATH, HOLDINGS_PATH, LOGIN_PATH, SEARCH_PATH, USER_AGENT
from .normalize import normalize_booking, normalize_history, normalize_item_detail, normalize_loan, normalize_search_result

_TRACE_LOG = logging.getLogger("cifereca")

class ParisError(RuntimeError):
    pass

class AuthenticationError(ParisError):
    pass

class ParisClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 25.0, diagnostics: Diagnostics | None = None,
                 client_factory: Callable[[], httpx.Client] | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.diagnostics = diagnostics or Diagnostics()
        self._client_factory = client_factory
        self._http_profile = "browser"
        self.client = self._create_http_client(self._http_profile)
        self.authenticated = False
        self.username: str | None = None
        self.last_search_warnings: list[str] = []

    def _create_http_client(self, profile: str = "browser") -> httpx.Client:
        if self._client_factory is not None:
            return self._client_factory()
        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
        }
        limits = httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=20.0)
        if profile == "browser":
            # Le script HTTP local qui a validé l'architecture s'exécute avec
            # Brotli disponible. On rend ici ce profil explicite afin que la
            # virtualenv n'envoie pas une requête différente par inadvertance.
            headers["Accept-Encoding"] = "gzip, deflate, br"
        elif profile == "compat":
            # Fallback unique pour certains reverse proxies qui ferment une
            # réponse avant son body : pas de compression et pas de keep-alive.
            headers["Accept-Encoding"] = "identity"
            headers["Connection"] = "close"
            limits = httpx.Limits(max_connections=10, max_keepalive_connections=0)
        return httpx.Client(
            headers=headers,
            follow_redirects=True,
            timeout=httpx.Timeout(self.timeout),
            limits=limits,
            http1=True,
            http2=False,
        )

    def _reset_http_client(self, profile: str | None = None) -> None:
        """Abandonne une connexion potentiellement corrompue et repart avec un cookie jar vide."""
        try:
            self.client.close()
        except Exception:
            pass
        if profile is not None:
            self._http_profile = profile
        self.client = self._create_http_client(self._http_profile)
        self.authenticated = False

    def _log_http_stack(self) -> None:
        def version(name: str) -> str:
            try:
                return metadata.version(name)
            except Exception:
                return "unknown"
        try:
            import brotli  # noqa: F401
            brotli_available = True
        except Exception:
            brotli_available = False
        self.diagnostics.add(
            "HTTP", "stack", "info",
            detail={
                "profile": self._http_profile,
                "httpx": version("httpx"),
                "httpcore": version("httpcore"),
                "brotli": brotli_available,
                "accept_encoding": self.client.headers.get("Accept-Encoding", ""),
                "http2": False,
                "bootstrap": "skipped",
            },
            level="detailed",
        )

    def close(self) -> None:
        self.client.close()

    def _api_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
            "Referer": f"{self.base_url}/numerique/account.aspx",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": USER_AGENT,
        }

    def _params(self, provider: str, uid: str = "") -> dict[str, str]:
        now = str(int(time.time() * 1000))
        return {"serviceCode": provider, "token": now, "userUniqueIdentifier": uid, "timestamp": now}

    @staticmethod
    def _error_text(value: Any) -> str | None:
        """Transforme les erreurs Syracuse structurées en texte utilisateur lisible."""
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return text or None
        if isinstance(value, dict):
            for key in ("msg", "message", "Message", "description", "id"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
            return None
        return str(value)

    def _json(self, response: httpx.Response, operation: str, require_success: bool = True) -> dict[str, Any]:
        try:
            data = response.json()
        except Exception as exc:
            raise ParisError(f"{operation}: réponse non JSON (HTTP {response.status_code})") from exc
        if not isinstance(data, dict):
            raise ParisError(f"{operation}: JSON inattendu")
        if require_success and (not response.is_success or data.get("success") is not True):
            errors = data.get("errors")
            first_error = errors[0] if isinstance(errors, list) and errors else None
            message = self._error_text(data.get("message")) or self._error_text(first_error) or f"HTTP {response.status_code}"
            raise ParisError(f"{operation}: {message}")
        return data

    def _timed(self, category: str, operation: str, fn):
        start = time.perf_counter()
        try:
            result = fn()
            self.diagnostics.add(category, operation, "ok", int((time.perf_counter()-start)*1000))
            return result
        except Exception as exc:
            self.diagnostics.add(category, operation, "error", int((time.perf_counter()-start)*1000), {"error": type(exc).__name__, "message": str(exc)[:240]})
            raise

    def bootstrap(self) -> int:
        return self._timed("HTTP", "bootstrap", lambda: self.client.get(f"{self.base_url}/numerique/").status_code)

    def login(self, username: str, password: str) -> dict[str, Any]:
        def post_login() -> dict[str, Any]:
            headers = self._api_headers()
            headers.update({
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/numerique/",
            })
            r = self.client.post(
                f"{self.base_url}{LOGIN_PATH}",
                headers=headers,
                data={"username": username, "password": password, "rememberMe": "false"},
            )
            if r.status_code == 403:
                raise AuthenticationError("Paris refuse le login HTTP direct depuis cette machine (403)")
            data = self._json(r, "login", require_success=False)
            if not r.is_success or data.get("success") is not True:
                raise AuthenticationError(str(data.get("message") or "Identifiant ou mot de passe incorrect"))
            self.authenticated = True
            self.username = username
            return data

        def work():
            # Le bootstrap n'est pas requis : le POST logon crée lui-même les
            # cookies Syracuse. Le supprimer évite un aller-retour fragile et
            # aligne le chemin critique sur le strict minimum.
            if self._http_profile != "browser":
                self._reset_http_client("browser")
            else:
                try:
                    self.client.cookies.clear()
                except Exception:
                    pass
            self._http_profile = "browser"
            self._log_http_stack()
            for attempt in (1, 2):
                try:
                    return post_login()
                except httpx.RemoteProtocolError as exc:
                    if attempt >= 2:
                        self.diagnostics.add(
                            "AUTH", "login retry exhausted", "error",
                            detail={
                                "attempt": "2/2",
                                "profile": self._http_profile,
                                "error": type(exc).__name__,
                                "message": str(exc)[:240],
                            },
                        )
                        raise
                    self.diagnostics.add(
                        "AUTH", "login retry", "retry",
                        detail={
                            "attempt": "1/2",
                            "profile": self._http_profile,
                            "error": type(exc).__name__,
                            "message": str(exc)[:240],
                            "action": "retry_compat_identity_close",
                        },
                    )
                    self._reset_http_client("compat")
                    self._log_http_stack()
                    time.sleep(0.35)
            raise AssertionError("unreachable")

        return self._timed("AUTH", "login", work)

    def summary(self, provider: str = "SYRACUSE", uid: str = "") -> dict[str, Any]:
        def work():
            r = self.client.get(f"{self.base_url}{ACCOUNT_SERVICE}/RetrieveAccountSummary", params=self._params(provider, uid), headers=self._api_headers())
            data = self._json(r, f"summary {provider}")
            d = data.get("d") if isinstance(data.get("d"), dict) else {}
            return d.get("AccountSummary") if isinstance(d.get("AccountSummary"), dict) else d
        return self._timed("API", f"summary {provider}", work)

    def loans(self, provider: str, uid: str = "") -> list[Loan]:
        def work():
            r = self.client.get(f"{self.base_url}{ACCOUNT_SERVICE}/ListLoans", params=self._params(provider, uid), headers=self._api_headers())
            data = self._json(r, f"loans {provider}")
            rows = ((data.get("d") or {}).get("Loans") or [])
            return [normalize_loan(x, provider) for x in rows if isinstance(x, dict)]
        return self._timed("API", f"loans {provider}", work)

    def all_loans(self, uid: str = "") -> list[Loan]:
        rows = self.loans("DILICOM", uid) + self.loans("SYRACUSE", uid)
        return sorted(rows, key=lambda x: (x.due_at is None, x.due_at or "", x.title.casefold()))

    def history(self, provider: str, uid: str = "") -> list[HistoryItem]:
        def work():
            r = self.client.get(f"{self.base_url}{ACCOUNT_SERVICE}/ListHandings", params=self._params(provider, uid), headers=self._api_headers())
            data = self._json(r, f"history {provider}")
            rows = ((data.get("d") or {}).get("Handings") or [])
            return [normalize_history(x, provider) for x in rows if isinstance(x, dict)]
        return self._timed("API", f"history {provider}", work)

    def all_history(self, uid: str = "") -> list[HistoryItem]:
        rows = self.history("DILICOM", uid) + self.history("SYRACUSE", uid)
        return sorted(rows, key=lambda x: x.borrowed_at or x.returned_at or "", reverse=True)

    def bookings(self, provider: str, uid: str = "") -> list[Booking]:
        def work():
            r = self.client.get(f"{self.base_url}{ACCOUNT_SERVICE}/ListBookings", params=self._params(provider, uid), headers=self._api_headers())
            data = self._json(r, f"bookings {provider}")
            rows = ((data.get("d") or {}).get("Bookings") or [])
            return [normalize_booking(x, provider) for x in rows if isinstance(x, dict)]
        return self._timed("API", f"bookings {provider}", work)

    def all_bookings(self, uid: str = "") -> list[Booking]:
        return self.bookings("DILICOM", uid) + self.bookings("SYRACUSE", uid)

    def pairings(self) -> list[Account]:
        def work():
            seen: dict[str, Account] = {}
            for provider in ("SYRACUSE", "DILICOM"):
                now = str(int(time.time() * 1000))
                r = self.client.get(f"{self.base_url}{ACCOUNT_SERVICE}/ListUserPairings", params={"serviceCode": provider, "timestamp": now}, headers=self._api_headers())
                data = self._json(r, f"pairings {provider}")
                d = data.get("d") if isinstance(data.get("d"), dict) else {}
                for bucket in ("UserPairings", "UserReversePairings"):
                    for raw in d.get(bucket) or []:
                        if not isinstance(raw, dict) or int(raw.get("State") or 0) != 2:
                            continue
                        uid = str(raw.get("UserUniqueIdentifier") or "")
                        if uid and uid not in seen:
                            seen[uid] = Account(
                                display_name=str(raw.get("UserDisplayName") or "Compte lié"),
                                barcode=str(raw.get("UserBarcode") or ""),
                                unique_id=uid,
                                is_primary=False,
                            )
            return list(seen.values())
        return self._timed("API", "pairings", work)

    def _search_query(self, query: str, scenario: str, page: int, result_size: int = 50) -> dict[str, Any]:
        encoded = quote(query)
        return {
            "Page": page, "ResultSize": result_size, "QueryString": query, "SearchLabel": "",
            "ScenarioCode": scenario, "InitialSearch": True, "SearchContext": 0, "PageRange": 3,
            "Url": f"{self.base_url}/search.aspx?SC={scenario}&QUERY={encoded}&QUERY_LABEL=#/Search/(query:(InitialSearch:!t,Page:{page},PageRange:3,QueryString:'{encoded}',ResultSize:{result_size},ScenarioCode:{scenario},SearchContext:0,SearchLabel:''))",
        }

    def _search_scenario(self, query: str, scenario: str, page: int, result_size: int) -> list[SearchResult]:
        body = {"query": self._search_query(query, scenario, page, result_size), "sst": 4}
        r = self.client.post(
            f"{self.base_url}{SEARCH_PATH}",
            headers={**self._api_headers(), "Content-Type": "application/json; charset=UTF-8", "Origin": self.base_url, "Referer": f"{self.base_url}/search.aspx?SC={scenario}"},
            json=body,
        )
        data = self._json(r, f"search {scenario}")
        rows = ((data.get("d") or {}).get("Results") or [])
        return [x for x in (normalize_search_result(raw, scenario) for raw in rows if isinstance(raw, dict)) if x]

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
            lambda: self._search_scenario(query, scenario, page, size),
        )

    def search(self, query: str, scope: str = "all", page: int = 0) -> list[SearchResult]:
        """Recherche tolérante aux défaillances d'un des deux catalogues.

        Les requêtes très larges pouvaient demander ``ResultSize=-1`` et provoquer
        l'erreur métier SearchService « Votre recherche a échoué ». On borne
        désormais la page à 50 résultats/scénario, avec un retry plus conservateur
        à 20 résultats. En mode ``all``, une panne EBOOKS ne masque jamais les
        résultats physiques déjà récupérés.
        """
        scenarios = ["EBOOKS"] if scope == "digital" else ["CATALOGUE"] if scope == "physical" else ["CATALOGUE", "EBOOKS"]
        out: list[SearchResult] = []
        seen: set[str] = set()
        self.last_search_warnings: list[str] = []
        failures: list[tuple[str, Exception]] = []

        for scenario in scenarios:
            rows: list[SearchResult] | None = None
            try:
                rows = self._timed(
                    "CATALOG", f"search {scenario}",
                    lambda scenario=scenario: self._search_scenario(query, scenario, page, 50),
                )
            except ParisError as first_exc:
                # Retry borné : utile surtout pour EBOOKS sur les recherches larges.
                self.diagnostics.add(
                    "CATALOG", f"search {scenario} fallback", "retry",
                    detail={"result_size": 20, "message": str(first_exc)[:180]},
                    level="detailed",
                )
                try:
                    rows = self._timed(
                        "CATALOG", f"search {scenario} fallback",
                        lambda scenario=scenario: self._search_scenario(query, scenario, page, 20),
                    )
                except Exception as second_exc:
                    failures.append((scenario, second_exc))
                    self.last_search_warnings.append(
                        "Catalogue numérique momentanément indisponible" if scenario == "EBOOKS" else "Catalogue physique momentanément indisponible"
                    )
                    continue

            for item in rows or []:
                if item.id not in seen:
                    seen.add(item.id)
                    out.append(item)

        if failures and not out:
            if scope == "digital":
                raise ParisError("Le catalogue numérique n'a pas répondu à cette recherche. Essayez un terme plus précis ou réessayez dans quelques instants.") from failures[-1][1]
            if scope == "physical":
                raise ParisError("Le catalogue physique n'a pas répondu à cette recherche. Réessayez dans quelques instants.") from failures[-1][1]
            raise ParisError("Les catalogues Paris n'ont pas répondu à cette recherche. Réessayez dans quelques instants.") from failures[-1][1]
        return out

    def holdings(self, docbase: str, record_id: str, scenario: str, search_terms: str = "") -> ItemDetail:
        def work():
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
            data = self._json(r, "holdings")
            return normalize_item_detail(docbase, record_id, data)
        return self._timed("CATALOG", "holdings", work)

    def pnb_preferences(self) -> dict[str, Any]:
        def work():
            r = self.client.get(f"{self.base_url}{ACCOUNT_SERVICE}/GetUserPnbPreferences", headers=self._api_headers())
            data = self._json(r, "PNB preferences", require_success=False)
            return data
        return self._timed("PNB", "preferences", work)

    def download_digital_document(self, loan: Loan, uid: str = "", preferences: dict[str, Any] | None = None) -> str:
        if loan.provider != "DILICOM" or not loan.record_id:
            raise ParisError("Ce prêt n'est pas un document numérique DILICOM téléchargeable")
        preferences = preferences or self.pnb_preferences()
        passphrase = preferences.get("PassPhrase") or ""
        hint = preferences.get("Hint") or ""
        reader_id = preferences.get("ReaderId") or ""
        if not reader_id:
            raise ParisError("Aucun ReaderId PNB n'est configuré sur le compte")
        def work():
            endpoint = f"{self.base_url}{ACCOUNT_SERVICE}/DownloadDigitalDocument"
            _TRACE_LOG.info(
                "TRACE download paris endpoint request loan_id=%s ean=%s uid_present=%s reader_id_present=%s endpoint_path=%s",
                loan.id, loan.record_id, bool(uid), bool(reader_id), ACCOUNT_SERVICE + "/DownloadDigitalDocument",
            )
            r = self.client.post(
                endpoint,
                headers={**self._api_headers(), "Content-Type": "application/json; charset=UTF-8"},
                json={
                    "serviceCode": loan.provider,
                    "ean": loan.record_id,
                    "userUniqueIdentifier": uid,
                    "actualPassPhrase": passphrase,
                    "actualHint": hint,
                    "actualReaderId": reader_id,
                },
            )
            _TRACE_LOG.info(
                "TRACE download paris endpoint response loan_id=%s status=%s content_type=%s bytes=%s",
                loan.id, r.status_code, r.headers.get("content-type", ""), len(r.content or b""),
            )
            data = self._json(r, "download digital document")
            value = data.get("d")
            if not isinstance(value, str) or not value:
                raise ParisError("Paris n'a pas renvoyé d'URL de téléchargement")
            return value
        return self._timed("PNB", "download digital document", work)


    def cancel_booking(self, booking: Booking, uid: str = "") -> dict[str, Any]:
        if not booking.raw:
            raise ParisError("L'objet réservation Syracuse brut est requis pour l'annulation")
        def work():
            r = self.client.post(
                f"{self.base_url}{ACCOUNT_SERVICE}/CancelBookings",
                headers={**self._api_headers(), "Content-Type": "application/json; charset=UTF-8"},
                json={"bookings": [booking.raw], "userUniqueIdentifier": uid},
            )
            return self._json(r, "cancel booking")
        return self._timed("ACTION", "cancel booking", work)

    def save_pnb_preferences(self, passphrase: str, hint: str, reader_id: str, reader_app_name: str, uid: str = "") -> dict[str, Any]:
        """Interface Syracuse découverte dans le JS officiel.

        Attention: Syracuse place ces valeurs sensibles dans la query string.
        Cette méthode ne journalise jamais les paramètres.
        """
        def work():
            r = self.client.get(
                f"{self.base_url}{ACCOUNT_SERVICE}/SavePnbPreferences",
                headers=self._api_headers(),
                params={
                    "passPhrase": passphrase,
                    "hint": hint,
                    "readerId": reader_id,
                    "readerAppName": reader_app_name,
                    "userUniqueIdentifier": uid,
                },
            )
            return self._json(r, "save PNB preferences", require_success=False)
        return self._timed("PNB", "save preferences", work)

    def check_availability_batch(self, records: list[dict[str, Any]], search_query: dict[str, Any]) -> list[Any]:
        def work():
            r = self.client.post(
                f"{self.base_url}{CHECK_AVAILABILITY_PATH}",
                headers={**self._api_headers(), "Content-Type": "application/json; charset=UTF-8", "Origin": self.base_url},
                json={"RecordIdArray": records, "searchQuery": search_query},
            )
            data = self._json(r, "check availability")
            return data.get("d") if isinstance(data.get("d"), list) else []
        return self._timed("CATALOG", "check availability batch", work)
    def renew_loan(self, loan: Loan, uid: str = "") -> dict[str, Any]:
        if not loan.renewable:
            raise ParisError(loan.cannot_renew_reason or "Ce prêt n'est pas renouvelable")
        def work():
            r = self.client.post(
                f"{self.base_url}{ACCOUNT_SERVICE}/RenewLoans",
                headers={**self._api_headers(), "Content-Type": "application/json; charset=UTF-8"},
                json={"loans": [loan.raw], "userUniqueIdentifier": uid},
            )
            return self._json(r, "renew loan")
        return self._timed("ACTION", "renew loan", work)

    def return_digital_loan(self, loan: Loan, uid: str = "") -> dict[str, Any]:
        if loan.provider != "DILICOM" or not loan.can_return:
            raise ParisError("Ce prêt numérique n'est pas marqué comme restituable")
        def work():
            r = self.client.post(
                f"{self.base_url}{ACCOUNT_SERVICE}/ReturnDigitalLoan",
                headers={**self._api_headers(), "Content-Type": "application/json; charset=UTF-8"},
                json={"serviceCode": "DILICOM", "id": loan.id, "userUniqueIdentifier": uid},
            )
            return self._json(r, "return digital loan")
        return self._timed("ACTION", "return digital loan", work)
