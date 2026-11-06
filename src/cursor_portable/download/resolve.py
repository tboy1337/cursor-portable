"""Resolve the official stable IDE download without saving it."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from urllib.parse import unquote, urlparse

from cursor_portable.download.catalog import api_download_url, ide_target
from cursor_portable.download.http_client import HttpClient, HttpResponse
from cursor_portable.failure import Failure

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedDownload:
    """A stable IDE file after redirects."""

    platform_id: str
    url: str
    file_name: str


def file_name_from_response(response: HttpResponse) -> str:
    """Prefer Content-Disposition, then the final URL path."""
    disposition = response.headers.get("content-disposition", "")
    marker = "filename="
    if marker in disposition.casefold():
        index = disposition.casefold().index(marker) + len(marker)
        raw = disposition[index:].strip().strip('"').split(";")[0].strip()
        if raw:
            return raw
    name = PathName.from_url(response.url)
    if name:
        return name
    return "cursor-download"


class PathName:
    """URL path helper kept as a class so the function stays small."""

    @staticmethod
    def from_url(url: str) -> str:
        """Return the last path segment."""
        path = unquote(urlparse(url).path)
        if not path or path.endswith("/"):
            return ""
        return path.rsplit("/", 1)[-1]


def resolve_ide(
    platform_id: str,
    client: HttpClient,
    *,
    timeout: float = 60.0,
) -> ResolvedDownload | Failure:
    """Follow the official download URL and return the final file name."""
    known = ide_target(platform_id)
    if isinstance(known, Failure):
        return known
    url = api_download_url(platform_id)
    LOGGER.info("Resolving %s via %s", platform_id, url)
    response = client.get(url, timeout=timeout)
    if isinstance(response, Failure):
        return response
    if response.status >= 400:
        return Failure("http", f"Download lookup returned HTTP {response.status}.")
    listed = _listed_download(response.body)
    if isinstance(listed, Failure):
        return listed
    if listed is not None:
        url, name = listed
        return ResolvedDownload(platform_id=platform_id, url=url, file_name=name)
    return ResolvedDownload(
        platform_id=platform_id,
        url=response.url,
        file_name=file_name_from_response(response),
    )


def _listed_download(body: bytes) -> tuple[str, str] | Failure | None:
    """Read ``downloadUrl`` when the lookup returns JSON instead of a file."""
    try:
        parsed: object = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    url = parsed.get("downloadUrl")
    if not isinstance(url, str) or not url.startswith("https://"):
        return Failure("http", "Download lookup did not include an HTTPS download URL.")
    name = PathName.from_url(url)
    if not name:
        return Failure("http", "Download URL has no file name.")
    return url, name
