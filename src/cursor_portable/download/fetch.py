"""Save a resolved download and check its size and hash."""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cursor_portable.download.http_client import HttpClient
from cursor_portable.download.verify_file import check_download
from cursor_portable.failure import Failure

LOGGER = logging.getLogger(__name__)

Progress = Callable[[str], None]


def download_file(
    url: str,
    destination: Path,
    client: HttpClient,
    *,
    minimum_bytes: int,
    expected_sha256: str | None = None,
    timeout: float = 600.0,
) -> Failure | None:
    """Download ``url`` to ``destination`` and verify it."""
    LOGGER.info("Downloading %s to %s", url, destination)
    response = client.get(url, timeout=timeout)
    if isinstance(response, Failure):
        return response
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.body)
    failure = check_download(
        destination,
        minimum_bytes=minimum_bytes,
        expected_sha256=expected_sha256,
    )
    if failure is not None:
        LOGGER.error("%s", failure.message)
    return failure


def download_progress_message(
    written: int,
    total: int | None,
    *,
    label: str | None = None,
) -> str:
    """Describe how much of a download has been saved."""
    amount = _size_text(written)
    subject = f"Downloading {label}: " if label else "Downloaded "
    if total is not None and total >= written and total >= 1024 * 1024:
        return f"{subject}{amount} of {_size_text(total)}"
    return f"{subject}{amount}"


def download_https(
    url: str,
    destination: Path,
    *,
    minimum_bytes: int,
    expected_sha256: str | None = None,
    timeout: float = 1800.0,
    progress: Progress | None = None,
    progress_label: str | None = None,
) -> Failure | None:
    """Stream an HTTPS file to ``destination`` and verify its size and hash."""
    if destination.is_file():
        ready = check_download(
            destination,
            minimum_bytes=minimum_bytes,
            expected_sha256=expected_sha256,
        )
        if ready is None:
            LOGGER.info("Keeping existing download %s", destination)
            return None
    if not url.startswith("https://"):
        return Failure("http", f"Refusing non-HTTPS URL: {url}")
    LOGGER.info("Downloading %s to %s", url, destination)
    request = Request(url, headers={"User-Agent": "cursor-portable"})
    partial = destination.with_name(destination.name + ".part")
    try:
        with urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
            if not str(final_url).startswith("https://"):
                return Failure(
                    "http", f"Refusing redirect to non-HTTPS URL: {final_url}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            _write_response(
                response,
                partial,
                progress,
                label=progress_label,
                total=_content_length(response),
            )
    except HTTPError as exc:
        LOGGER.error("HTTP %s for %s", exc.code, url)
        exc.close()
        return Failure("http", f"HTTP {exc.code} for {url}")
    except (URLError, OSError, TimeoutError) as exc:
        LOGGER.error("Request failed for %s: %s", url, exc)
        return Failure("http", f"Request failed for {url}: {exc}")
    partial.replace(destination)
    failure = check_download(
        destination,
        minimum_bytes=minimum_bytes,
        expected_sha256=expected_sha256,
    )
    if failure is not None:
        LOGGER.error("%s", failure.message)
    return failure


def _size_text(written: int) -> str:
    if written >= 1024 * 1024:
        return f"{written // (1024 * 1024)} MB"
    if written >= 1024:
        return f"{written // 1024} KB"
    return f"{written} bytes"


def _content_length(response: object) -> int | None:
    headers = getattr(response, "headers", None)
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    raw = getter("Content-Length")
    if isinstance(raw, int) and raw >= 0:
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return None


def _write_response(
    response: object,
    partial: Path,
    progress: Progress | None,
    *,
    label: str | None,
    total: int | None,
) -> None:
    read = getattr(response, "read")
    written = 0
    next_report = 8 * 1024 * 1024
    with partial.open("wb") as handle:
        while True:
            chunk = read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            written += len(chunk)
            if progress is not None and written >= next_report:
                progress(download_progress_message(written, total, label=label))
                next_report += 8 * 1024 * 1024
    if progress is not None and written > 0:
        progress(download_progress_message(written, total, label=label))
