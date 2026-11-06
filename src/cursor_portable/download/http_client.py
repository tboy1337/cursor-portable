"""Small HTTP client used by download resolution."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cursor_portable.failure import Failure

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class HttpResponse:
    """A completed HTTP response."""

    status: int
    url: str
    body: bytes
    headers: Mapping[str, str]


class HttpClient(Protocol):
    """Fetches one URL."""

    def get(self, url: str, *, timeout: float) -> HttpResponse | Failure:
        """Return the response or a failure."""


class UrllibClient:
    """HTTPS client. Redirects are followed by urllib."""

    def get(
        self,
        url: str,
        *,
        timeout: float,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse | Failure:
        """GET ``url`` and return the body."""
        LOGGER.info("GET %s", url)
        if not url.startswith("https://"):
            return Failure("http", f"Refusing non-HTTPS URL: {url}")
        request_headers = {"User-Agent": "cursor-portable"}
        if headers:
            request_headers.update(headers)
        request = Request(url, headers=request_headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                final_url = response.geturl()
                body = response.read()
                headers = {
                    key.lower(): value for key, value in response.headers.items()
                }
        except HTTPError as exc:
            LOGGER.error("HTTP %s for %s", exc.code, url)
            exc.close()
            return Failure("http", f"HTTP {exc.code} for {url}")
        except URLError as exc:
            LOGGER.error("Request failed for %s: %s", url, exc.reason)
            return Failure("http", f"Request failed for {url}: {exc.reason}")
        if not isinstance(status, int):
            return Failure("http", f"Unexpected status for {url}")
        if not final_url.startswith("https://"):
            LOGGER.error("Refusing redirect from %s to %s", url, final_url)
            return Failure("http", f"Refusing redirect to non-HTTPS URL: {final_url}")
        return HttpResponse(status=status, url=final_url, body=body, headers=headers)
