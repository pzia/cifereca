"""PNB operations on the existing Paris client; no separate transport or session."""
from __future__ import annotations

import logging
import hashlib
import contextvars
import json
import time
import uuid
import html
import ipaddress
import re
from html.parser import HTMLParser
from importlib import metadata
from dataclasses import asdict, replace
from typing import Any, Iterable, Callable
from urllib.parse import quote, urljoin, urlparse, unquote
import httpx
from cifereca.plugin_api import HTTPX_TRANSPORT
from cifereca.plugin_api import Account, HistoryItem, ItemDetail, Loan, SearchResult, AttentionRequired
from cifereca_paris_constants import ACCOUNT_SERVICE, BASE_URL, CHECK_AVAILABILITY_PATH, HOLDINGS_PATH, LOGIN_PATH, SEARCH_PATH
from cifereca_paris_trace import (
    public_book_id, public_content_type, public_http_status, public_site,
)
from cifereca_paris_normalize import normalize_history, normalize_item_detail, normalize_loan, normalize_search_result
from cifereca_paris_bibliography import project_search_response_facets


class ParisPnbOperations:
    def _set_pnb_borrow_phase(self, phase: str, **facts: Any) -> None:
        safe: dict[str, Any] = {"phase": str(phase)}
        for key, value in facts.items():
            if key in {"record_id", "url", "href", "location", "token", "cookie", "authorization"}:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe[str(key)] = value
        self.last_pnb_borrow = safe
        try:
            self.diagnostics.add("PNB", "borrow phase", "ok" if phase not in {"failed"} else "error", 0, safe)
        except Exception:
            pass

    @staticmethod
    def _pnb_loan_href(payload: dict[str, Any]) -> str:
        """Extract the official pnb-loan ASSARedirect href from HtmlView."""
        from cifereca_paris_client import _TRACE_LOG, ParisError
        d = payload.get("d") if isinstance(payload.get("d"), dict) else {}
        item_holding = d.get("ItemHoldingsData") if isinstance(d.get("ItemHoldingsData"), dict) else {}
        if item_holding.get("CanLoan") is not True:
            _TRACE_LOG.warning("TRACE paris pnb-loan blocked phase=availability can_loan=false_or_missing")
            label = str(item_holding.get("HoldingLabel") or "indisponible")
            raise ParisError(f"Emprunt PNB impossible : {label}")
        html_view = d.get("HtmlView")
        if not isinstance(html_view,str):html_view=""
        # Parse HTML attributes, not a regex requiring a specific quote style.
        # Decode only for classification: use the server's original URL for navigation.
        class Links(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.hrefs = []
                self.forms = 0
                self.post_forms = 0
                self.form_actions = 0

            def handle_starttag(self, tag, attrs):
                attrs = {str(k).casefold(): v for k, v in attrs if k is not None}
                if tag.casefold() == "a":
                    href = attrs.get("href")
                    if type(href) is str and href.strip():
                        self.hrefs.append(href.strip())
                elif tag.casefold() == "form":
                    self.forms += 1
                    if str(attrs.get("method") or "").casefold() == "post":
                        self.post_forms += 1
                    if type(attrs.get("action")) is str and attrs["action"].strip():
                        self.form_actions += 1

        # Keep all actionable candidates under the pre-existing exact
        # selector. The counters below are closed diagnostic classes only:
        # observing a form, a DOM marker, or an unknown host does NOT grant
        # permission to navigate, send a POST or infer a signed borrow URL.
        links = Links()
        links.feed(html_view[:2_000_000])
        candidates = []
        candidate_requests = set()
        assaredirect_hrefs = 0
        loan_book_hrefs = 0
        rejected_scheme = 0
        malformed_hrefs = 0
        external_hrefs = 0
        rejected_candidate_origin = 0
        expected_host = urlparse(BASE_URL).hostname
        for href in links.hrefs:
            decoded = href
            for _ in range(2):
                decoded = unquote(decoded)
            try:
                parsed = urlparse(decoded)
                href_site = public_site(urljoin(BASE_URL, decoded))
            except ValueError:
                malformed_hrefs += 1
                continue
            if parsed.scheme and parsed.scheme.casefold() not in {"http", "https"}:
                rejected_scheme += 1
            if href_site == "external":
                external_hrefs += 1
            path_known = parsed.path.casefold().endswith("/assaredirect.ashx")
            loan_marker = "loan-book.ashx" in decoded.casefold()
            assaredirect_hrefs += int(path_known)
            loan_book_hrefs += int(loan_marker)
            if path_known and loan_marker:
                # A lookalike link is not a valid Paris borrow candidate.
                # Validate both the decoded shape used for discovery and the
                # ORIGINAL href passed to urljoin by the redirect resolver.
                # Never query either URL merely to verify its provenance.
                try:
                    original_target = urlparse(urljoin(BASE_URL + "/", href))
                    decoded_target = urlparse(urljoin(BASE_URL + "/", decoded))
                    valid_origin = all(
                        target.scheme == "https"
                        and target.hostname == expected_host
                        and target.username is None and target.password is None
                        and target.port in (None, 443)
                        for target in (original_target, decoded_target)
                    )
                except ValueError:
                    valid_origin = False
                if not valid_origin:
                    rejected_candidate_origin += 1
                    continue
                # HTML may offer the SAME official borrowing action twice
                # (relative and absolute URL, or explicit default HTTPS port).
                # Compare the exact HTTPS request target, not the href markup.
                # Preserve query order/encoding: distinct signed targets must
                # remain ambiguous, never select one implicitly.
                request = (
                    original_target.scheme.lower(),
                    original_target.hostname.lower(),
                    original_target.port or 443,
                    original_target.path, original_target.params,
                    original_target.query,
                )
                if request not in candidate_requests:
                    candidate_requests.add(request)
                    candidates.append(href)
        selected_site = (
            public_site(urljoin(BASE_URL, candidates[0]))
            if len(candidates) == 1 else "unknown"
        )
        _TRACE_LOG.info(
            "TRACE paris pnb-loan link-discovery can_loan=true "
            "html_present=%s html_chars=%s anchors=%s candidates=%s "
            "forms=%s post_forms=%s form_actions=%s "
            "assaredirect_hrefs=%s loan_book_hrefs=%s "
            "dom_assaredirect=%s dom_loan_book=%s "
            "rejected_scheme=%s malformed_hrefs=%s external_hrefs=%s "
            "rejected_candidate_origin=%s candidate_site=%s",
            bool(html_view), len(html_view), len(links.hrefs), len(candidates),
            links.forms, links.post_forms, links.form_actions,
            assaredirect_hrefs, loan_book_hrefs,
            "assaredirect.ashx" in html_view.casefold(),
            "loan-book.ashx" in html_view.casefold(),
            rejected_scheme, malformed_hrefs, external_hrefs,
            rejected_candidate_origin, selected_site,
        )
        if len(candidates)==1:return candidates[0]
        reason='ambiguous_link' if candidates else 'missing_link'
        _TRACE_LOG.warning("TRACE paris pnb-loan blocked phase=link-discovery reason=%s",reason)
        raise ParisError("GetListHoldings n'a pas fourni un lien officiel d'emprunt PNB unique "
                         "(lien absent ou ambigu). Aucun emprunt lancé ; actualisez la disponibilité ou vérifiez le parcours sur le site Paris.")

    def _validate_pnb_redirect_url(self, url: str) -> None:
        """Host-neutral validation of one supplier-signed HTTPS redirect.

        This does NOT authorize an arbitrary URL: callers first attest the
        unique Paris ASSARedirect origin. A foreign GET is made solely through
        Core's peer-pinned public transport, without private session headers.
        """
        from cifereca_paris_client import _TRACE_LOG, ParisRedirectError
        host = ""  # Malformed/non-string URLs cannot leak or raise NameError.
        try:
            if (type(url) is not str or not 0 < len(url) <= 16384
                    or any(ord(c) < 33 or ord(c) == 127 or c == chr(92) or c.isspace()
                           for c in url)):
                raise ValueError
            parsed = urlparse(url)
            host = (parsed.hostname or "").casefold()
            if (parsed.scheme != "https" or parsed.port not in (None, 443)
                    or parsed.username is not None or parsed.password is not None
                    or parsed.fragment or not host or host.endswith(".")
                    or "%" in parsed.netloc or host in {"localhost", "metadata.google.internal"}
                    or host.endswith((".localhost", ".local", ".internal", ".home.arpa", ".invalid"))):
                raise ValueError
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                raise ValueError
            if (len(host) > 253 or len(host.split(".")) < 2
                    or any(re.fullmatch(
                        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label,
                    ) is None for label in host.split("."))):
                raise ValueError
        except (ValueError, AttributeError, TypeError):
            # Arbitrary subdomains can contain account IDs or bearer tokens.
            # Only the closed public_site() label may enter a diagnostic.
            _TRACE_LOG.warning(
                "PNB redirect refused site=%s reason=invalid_https_destination",
                public_site(url),
            )
            raise ParisRedirectError(host) from None

    @staticmethod
    def _pnb_trace_path(url: str) -> str:
        """Return a log-safe path; DILICOM LOAN URLs carry opaque tokens."""
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        if host == "pnb-dilicom.centprod.com" and "/LOAN/" in parsed.path.upper():
            prefix = re.split(r"/LOAN/", parsed.path, maxsplit=1, flags=re.I)[0]
            return f"{prefix}/LOAN/<redacted>"
        if host in {"www.edenlivres.fr", "www.cantook.net", "acs4.edelivres.fr", "acs4.edenlivres.fr", "acs4.cantook.net"}:
            return "/<redacted>"
        # Delivery paths can contain opaque credentials on any supplier.
        if parsed.path.endswith("ASSARedirect.ashx"):
            return "/ASSARedirect.ashx"
        return "/<redacted>"

    @staticmethod
    def _pnb_trace_response(response, *, hop, elapsed_ms):
        from cifereca_paris_client import _TRACE_LOG
        location = response.headers.get("location") or ""
        try:
            target = urlparse(urljoin(str(response.url), location)) if location else None
            site = public_site(target.geturl()) if target else "unknown"
            scheme = target.scheme if target and target.scheme in {"https", "http"} else ("other" if target else None)
            port = target.port if target else None
            acsm = bool(target and target.path.casefold().endswith(".acsm"))
        except ValueError:
            site, scheme, port, acsm = "unknown", "invalid", None, False
        _TRACE_LOG.info(
            "TRACE paris pnb-loan hop-response hop=%s status=%s target_site=%s target_scheme=%s target_port=%s target_acsm=%s dt_ms=%s",
            hop, public_http_status(response.status_code), site, scheme, port, acsm, elapsed_ms,
        )

    def _get_pnb_response(self, url, **kwargs):
        from cifereca_paris_client import _MAX_PNB_PUBLIC_BODY_BYTES, ParisError, ParisRedirectError, ParisContentTransferUnavailable, HTTPX_TRANSPORT
        # Only Paris receives authenticated cookies. External signed redirects
        # use Core's public, peer-pinned transport with no private headers.
        # A selected canonical acquisition may additionally pass its live,
        # revocable admission lease; legacy callers still use the same API.
        admission_current = kwargs.pop("admission_current", None)

        def require_admission():
            if admission_current is None:
                return
            try:
                if callable(admission_current) and admission_current() is True:
                    return
            except Exception:
                pass
            raise ParisError("Paris PNB Core admission revoked")

        require_admission()
        self._validate_pnb_redirect_url(url)
        host = (urlparse(url).hostname or "").casefold()
        base_host = (urlparse(self.base_url).hostname or "").casefold()

        def bounded_response(response):
            if response.status_code != 200:
                payload = b""
            else:
                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        size = int(declared)
                    except (TypeError, ValueError):
                        raise ParisError("Taille de réponse PNB invalide") from None
                    if not 0 <= size <= _MAX_PNB_PUBLIC_BODY_BYTES:
                        raise ParisError("Réponse HTTPS PNB trop volumineuse")
                chunks = bytearray()
                iterator = iter(response.iter_bytes(chunk_size=64 * 1024))
                while True:
                    # Check BEFORE pulling the next network chunk, not after
                    # accepting its bytes. OFF→ON still invalidates this lease.
                    require_admission()
                    try:
                        chunk = next(iterator)
                    except StopIteration:
                        break
                    except httpx.RemoteProtocolError:
                        # At this point an HTTP 200 response has already
                        # been admitted and its body is being consumed.
                        # This is not a malformed redirect/Location. Only
                        # the selected delivery caller may qualify replay,
                        # after rechecking its live Core and owner scope.
                        require_admission()
                        raise ParisContentTransferUnavailable(
                            "Transport HTTPS PNB indisponible",
                        ) from None
                    require_admission()
                    if len(chunks) + len(chunk) > _MAX_PNB_PUBLIC_BODY_BYTES:
                        raise ParisError("Réponse HTTPS PNB trop volumineuse")
                    chunks.extend(chunk)
                payload = bytes(chunks)
            require_admission()
            return httpx.Response(
                response.status_code, headers=response.headers,
                content=payload, request=response.request,
            )

        try:
            if host == base_host:
                if admission_current is None:
                    # Preserve historical/manual semantics and custom
                    # transports for callers without a canonical Core lease.
                    return self.client.get(url, follow_redirects=False, **kwargs)
                with self.client.stream("GET", url,
                                        follow_redirects=False, **kwargs) as response:
                    return bounded_response(response)
            headers = dict(kwargs.get("headers") or {})
            for name in list(headers):
                if name.casefold() not in {"accept", "accept-language", "user-agent"}:
                    del headers[name]
            with HTTPX_TRANSPORT.public_client(
                producer="provider.paris.pnb.delivery", timeout=self.timeout,
            ) as public:
                # Redirect/error bodies never become candidate content.
                with public.stream("GET", url, headers=headers,
                                   follow_redirects=False) as response:
                    return bounded_response(response)
        except (httpx.InvalidURL, httpx.RemoteProtocolError):
            # HTTPX can embed a signed Location or request URL in exception
            # messages, even when follow_redirects=False. Never journal it.
            raise ParisRedirectError(public_site(url)) from None
        except (httpx.TimeoutException, httpx.NetworkError):
            raise ParisContentTransferUnavailable("Transport HTTPS PNB indisponible") from None
        except httpx.HTTPError:
            # Includes stream/decode and signed URL/nonce failures; expose
            # only a closed, retryable reason instead of a private traceback.
            raise ParisError("Transport HTTPS PNB indisponible") from None

    def _resolve_pnb_loan_to_acsm(self, href: str, *, max_redirects: int = 12,
                                  admission_current=None) -> str:
        """Follow the official signed PNB redirect chain and stop before ACSM GET."""
        from cifereca_paris_client import _TRACE_LOG, ParisError, ParisRedirectError
        current = urljoin(self.base_url + "/", href)
        base_host = (urlparse(self.base_url).hostname or "").casefold()
        parsed_start = urlparse(current)
        if (parsed_start.hostname or "").casefold() != base_host or "ASSARedirect.ashx" not in parsed_start.path:
            raise ParisError("Lien PNB initial invalide: ASSARedirect attendu sur Bibliothèques de Paris")

        navigation_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
            "Referer": f"{self.base_url}/numerique/",
        }
        visited_urls: set[str] = set()
        for hop in range(max_redirects):
            if admission_current is not None and admission_current() is not True:
                raise ParisError("paris_borrow_scope_changed")
            self._validate_pnb_redirect_url(current)
            if current in visited_urls:
                # Never replay a signed supplier URL in a redirect cycle.
                # The URL can contain a bearer token: never echo it.
                raise ParisError("Boucle de redirections PNB détectée")
            visited_urls.add(current)
            parsed = urlparse(current)
            _TRACE_LOG.info(
                "TRACE paris pnb-loan redirect-request hop=%s site=%s",
                hop + 1, public_site(current),
            )
            hop_started = time.monotonic()
            # A foreign supplier never receives Paris Referer, cookies or
            # Authorization, even when the preceding signed hop was Paris.
            hop_headers = (navigation_headers if
                (parsed.hostname or "").casefold() == base_host else {
                    key: value for key, value in navigation_headers.items()
                    if key.casefold() in {"accept", "accept-language", "user-agent"}
                })
            response = self._get_pnb_response(
                current, headers=hop_headers,
                **({"admission_current": admission_current}
                   if admission_current is not None else {}),
            )
            if admission_current is not None and admission_current() is not True:
                raise ParisError("paris_borrow_scope_changed")
            self._pnb_trace_response(response, hop=hop + 1, elapsed_ms=int((time.monotonic()-hop_started)*1000))
            location = response.headers.get("location") or ""
            _TRACE_LOG.info(
                "TRACE paris pnb-loan redirect-response hop=%s status=%s site=%s has_location=%s",
                hop + 1, public_http_status(response.status_code), public_site(current), bool(location),
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                raise ParisError(
                    f"Parcours PNB interrompu avant l'ACSM (HTTP {response.status_code} sur {public_site(current)})"
                )
            if not location:
                raise ParisError("Parcours PNB interrompu: redirection sans en-tête Location")
            # urljoin() discards CR, LF and TAB in raw Location values.
            # Do not silently rewrite a supplier-signed redirect into a
            # different request. Reject before URL parsing or another GET.
            if (type(location) is not str or len(location) > 16384
                    or any(ord(c) < 33 or ord(c) == 127 or c == chr(92)
                           or c.isspace() for c in location)):
                raise ParisRedirectError("invalid")
            next_url = urljoin(str(response.url), location)
            self._validate_pnb_redirect_url(next_url)
            next_parsed = urlparse(next_url)
            if next_parsed.path.casefold().endswith(".acsm") or "urllink.acsm" in next_parsed.path.casefold():
                _TRACE_LOG.info(
                    "TRACE paris pnb-loan acsm-ready hop=%s site=%s",
                    hop + 1, public_site(next_url),
                )
                return next_url
            current_host = (urlparse(str(response.url)).hostname or "").casefold()
            next_host = (next_parsed.hostname or "").casefold()
            if current_host == next_host == base_host:
                navigation_headers["Referer"] = str(response.url)
            else:
                # Never disclose a signed path or initial Paris session to
                # an unrelated supplier. Re-entry into Paris gets no implicit
                # authentication-derived Referer from an untrusted CDN.
                navigation_headers.pop("Referer", None)
            current = next_url
        raise ParisError(f"Parcours PNB trop long ({max_redirects} redirections) sans URL ACSM")

    def borrow_digital(self, item: SearchResult, uid: str = "") -> Loan:
        """Create a real DILICOM loan through the captured official PNB flow."""
        from cifereca_paris_client import _TRACE_LOG, _BORROW_TRACE, ParisError
        if item.docbase.upper() != "DILICOM" or item.medium != "digital" or not item.record_id:
            raise ParisError("Seuls les livres numériques DILICOM peuvent être empruntés via ce parcours")
        if uid:
            raise ParisError("L'emprunt direct PNB d'un compte délégué n'est pas encore validé")

        record_ref = hashlib.sha256(str(item.record_id).encode()).hexdigest()[:12]
        self._set_pnb_borrow_phase("start", record_ref=record_ref, delegated=False)

        def work() -> Loan:
            self._set_pnb_borrow_phase("existing-loan", record_ref=record_ref)
            _TRACE_LOG.info("TRACE paris pnb-loan begin docbase=DILICOM phase=existing-loan")
            existing = next((row for row in self.loans("DILICOM", uid)
                if str(row.record_id or "") == str(item.record_id)), None)
            if existing is not None:
                self._set_pnb_borrow_phase("completed", record_ref=record_ref, mutation=False, existing=True)
                _TRACE_LOG.info("TRACE paris pnb-loan existing-confirmed mutation=skipped")
                return existing
            self._set_pnb_borrow_phase("holdings", record_ref=record_ref)
            payload = self._holdings_payload(item.docbase, item.record_id, item.scenario or "EBOOKS", item.title)
            _TRACE_LOG.info("TRACE paris pnb-loan phase=holdings-read payload_dict=%s d_dict=%s",isinstance(payload,dict),isinstance(payload.get('d'),dict))
            self._set_pnb_borrow_phase("link-discovery", record_ref=record_ref)
            href = self._pnb_loan_href(payload)
            self._set_pnb_borrow_phase("redirect", record_ref=record_ref, mutation=True)
            _TRACE_LOG.info("TRACE paris pnb-loan phase=redirect-dispatch mutation_requested=true")
            acsm_url = self._resolve_pnb_loan_to_acsm(href)
            self._set_pnb_borrow_phase("provider-confirmation", record_ref=record_ref, mutation=True)
            for attempt in range(4):
                rows = self.loans("DILICOM", uid)
                loan = next((row for row in rows if str(row.record_id or "") == str(item.record_id)), None)
                _TRACE_LOG.info("TRACE paris pnb-loan phase=provider-confirmation attempt=%s confirmed=%s results=%s",attempt+1,loan is not None,len(rows))
                if loan is not None:
                    loan.ebook_url = acsm_url
                    self._set_pnb_borrow_phase("completed", record_ref=record_ref, mutation=True, existing=False, confirmation_attempt=attempt+1)
                    return loan
                if attempt < 3:
                    time.sleep(0.35 * (attempt + 1))
            raise ParisError(
                "Le serveur PNB a exécuté le parcours d'emprunt mais ListLoans(DILICOM) n'a pas confirmé le prêt"
            )

        trace=uuid.uuid4().hex[:12];token=_BORROW_TRACE.set(trace)
        _TRACE_LOG.info("TRACE paris pnb-loan operation=borrow record_ref=%s delegated=false",record_ref)
        try:
            return self._timed("PNB", "borrow digital", work)
        except Exception as exc:
            previous = str((getattr(self, "last_pnb_borrow", {}) or {}).get("phase") or "unknown")
            self._set_pnb_borrow_phase("failed", record_ref=record_ref, failed_from=previous, error=type(exc).__name__)
            raise
        finally:
            _BORROW_TRACE.reset(token)

    def pnb_preferences(self) -> dict[str, Any]:
        def work():
            r = self.client.get(f"{self.base_url}{ACCOUNT_SERVICE}/GetUserPnbPreferences", headers=self._api_headers())
            data = self._json(r, "PNB preferences", require_success=False)
            return data
        return self._timed("PNB", "preferences", work)

    def download_digital_document(self, loan: Loan, uid: str = "", preferences: dict[str, Any] | None = None) -> str:
        from cifereca_paris_client import _TRACE_LOG, ParisError
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
                "TRACE download paris endpoint request book_id=%s site=%s uid_present=%s reader_id_present=%s endpoint=DownloadDigitalDocument",
                public_book_id(loan.record_id), public_site(self.base_url),
                bool(uid), bool(reader_id),
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
                "TRACE download paris endpoint response book_id=%s site=%s status=%s content_type=%s bytes=%s",
                public_book_id(loan.record_id), public_site(self.base_url),
                public_http_status(r.status_code),
                public_content_type(r.headers.get("content-type", "")), len(r.content or b""),
            )
            data = self._json(r, "download digital document")
            value = data.get("d")
            if not isinstance(value, str) or not value:
                raise ParisError("Paris n'a pas renvoyé d'URL de téléchargement")
            return value
        return self._timed("PNB", "download digital document", work)


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

