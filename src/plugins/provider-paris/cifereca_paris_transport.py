"""Transport and authentication operations on the single Paris client."""
from __future__ import annotations
import time
from importlib import metadata
from typing import Any
import httpx
from cifereca_paris_constants import LOGIN_PATH


class ParisTransportOperations:
    def _create_http_client(self, profile: str = "browser") -> httpx.Client:
        from cifereca_paris_client import HTTPX_TRANSPORT
        if self._client_factory is not None:
            return self._client_factory()
        headers = {
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
        return HTTPX_TRANSPORT.client(producer="provider.paris",
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
        from cifereca_paris_client import ParisError
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
        from cifereca_paris_client import _TRACE_LOG, AuthenticationError
        start = time.perf_counter()
        try:
            result = fn()
            self.diagnostics.add(category, operation, "ok", int((time.perf_counter()-start)*1000))
            return result
        except Exception as exc:
            if category=="PNB" and operation=="borrow digital":
                # The existing canonical local TRACE event must retain the
                # *original* provider exception and traceback. A type-only
                # summary discards the reason a real PNB action failed.
                # Diagnostics UI pseudonymization belongs to Core, not here.
                _TRACE_LOG.warning(
                    "TRACE paris pnb-loan failed error=%s duration_ms=%s",
                    type(exc).__name__, int((time.perf_counter()-start)*1000),
                    exc_info=True,
                )
            detail = {"error": type(exc).__name__}
            # Keep the historical policy of unrelated operations unchanged.
            # ListLoans messages are actual provider facts and must not be
            # suppressed by the privacy mask introduced by #3413 (#3423).
            private_operation = (
                category == "AUTH"
                or (category == "API" and operation.startswith("summary "))
                or (category == "CATALOG" and (
                    operation.startswith("search ")
                    or operation.startswith("history cover search ")
                ))
            )
            if category == "AUTH" and isinstance(exc, AuthenticationError):
                detail["code"] = exc.code
            if (category == "API" and operation.startswith("summary ")
                    and isinstance(exc, ParisSummaryHTTPError)):
                detail["code"] = exc.code
            if (category == "API" and operation.startswith("loans ")):
                if isinstance(exc, ParisLoanListError):
                    detail["code"] = exc.code
                    detail["http_status"] = exc.http_status
                elif isinstance(exc, httpx.TimeoutException):
                    detail["code"] = "paris_loans_transport_timeout"
                elif isinstance(exc, httpx.RequestError):
                    detail["code"] = "paris_loans_transport_failed"
            if category == "API" and operation.startswith("loans "):
                # The exact observed ListLoans exception text belongs to the
                # raw journal and broker, including text beyond 240 chars.
                # It complements the structured reason and HTTP status.
                detail["message"] = str(exc)
            elif not private_operation:
                detail["message"] = str(exc)[:240]
            self.diagnostics.add(category, operation, "error", int((time.perf_counter()-start)*1000), detail)
            raise

    def bootstrap(self) -> int:
        return self._timed("HTTP", "bootstrap", lambda: self.client.get(f"{self.base_url}/numerique/").status_code)

    def login(self, username: str, password: str) -> dict[str, Any]:
        from cifereca_paris_client import AuthenticationError
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
                raise AuthenticationError(
                    "Paris refuse le login HTTP direct depuis cette machine (403)",
                    code="paris_login_http_forbidden",
                )
            if r.status_code == 401:
                raise AuthenticationError(
                    "Paris refuse l'authentification (401)",
                    code="paris_login_http_unauthorized",
                )
            if r.status_code == 429:
                raise AuthenticationError(
                    "Paris limite temporairement les connexions (429)",
                    code="paris_login_rate_limited",
                )
            if 500 <= r.status_code <= 599:
                raise AuthenticationError(
                    "Service d'authentification Paris indisponible",
                    code="paris_login_upstream_unavailable",
                )
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
                                "code": "paris_login_retry_exhausted",
                            },
                        )
                        raise
                    self.diagnostics.add(
                        "AUTH", "login retry", "retry",
                        detail={
                            "attempt": "1/2",
                            "profile": self._http_profile,
                            "error": type(exc).__name__,
                            "code": "paris_login_protocol_retry",
                            "action": "retry_compat_identity_close",
                        },
                    )
                    self._reset_http_client("compat")
                    self._log_http_stack()
                    time.sleep(0.35)
            raise AssertionError("unreachable")

        return self._timed("AUTH", "login", work)

