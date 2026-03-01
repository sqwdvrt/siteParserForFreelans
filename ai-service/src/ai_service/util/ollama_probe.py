"""Startup probe for Ollama connectivity."""

from __future__ import annotations

import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_SEC = 5


def probe_ollama(url: str, *, required: bool) -> bool:
    """Check that Ollama is reachable at *url*.

    Returns True on success.  On failure:
    - if *required* is True  → logs an error and returns False (caller must exit).
    - if *required* is False → logs a warning and returns False.

    Does not call sys.exit itself so callers retain control.
    """
    probe_url = url.rstrip("/") + "/"
    try:
        with urllib.request.urlopen(probe_url, timeout=_PROBE_TIMEOUT_SEC) as resp:
            if resp.status == 200:
                logger.info("ollama probe OK url=%s", url)
                return True
            logger.warning("ollama probe unexpected status=%d url=%s", resp.status, url)
    except (urllib.error.URLError, OSError) as exc:
        if required:
            logger.error(
                "ollama probe failed url=%s error=%s — "
                "Ollama is required but unreachable; set OLLAMA_REQUIRED=0 to "
                "allow rule-based fallback and suppress this error",
                url,
                exc,
            )
        else:
            logger.warning(
                "ollama probe failed url=%s error=%s — "
                "falling back to rule-based logic for all requests",
                url,
                exc,
            )
    return False
