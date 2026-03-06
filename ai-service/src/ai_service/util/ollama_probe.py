"""Startup probe for Ollama connectivity and model availability."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Iterable

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_SEC = 5


def probe_ollama(url: str, *, required: bool, required_models: Iterable[str] = ()) -> bool:
    """Check that Ollama is reachable at *url* and required models are present.

    Returns True on success.  On failure:
    - if *required* is True  → logs an error and returns False (caller must exit).
    - if *required* is False → logs a warning and returns False.

    Does not call sys.exit itself so callers retain control.
    """
    probe_url = url.rstrip("/") + "/api/tags"
    normalized_required_models = tuple(dict.fromkeys(model.strip() for model in required_models if model.strip()))
    try:
        with urllib.request.urlopen(probe_url, timeout=_PROBE_TIMEOUT_SEC) as resp:
            if resp.status == 200:
                payload = json.load(resp)
                available_models = {
                    str(model.get("name", "")).strip()
                    for model in payload.get("models", [])
                    if isinstance(model, dict)
                }
                available_models.update(
                    str(model.get("model", "")).strip()
                    for model in payload.get("models", [])
                    if isinstance(model, dict)
                )
                available_models.discard("")
                missing_models = [model for model in normalized_required_models if model not in available_models]
                if missing_models:
                    _log_probe_failure(
                        required=required,
                        message=(
                            "ollama probe missing required models url=%s missing=%s available=%s"
                            % (url, ",".join(missing_models), ",".join(sorted(available_models)) or "<none>")
                        ),
                    )
                    return False
                if normalized_required_models:
                    logger.info("ollama probe OK url=%s models=%s", url, ",".join(normalized_required_models))
                else:
                    logger.info("ollama probe OK url=%s", url)
                return True
            logger.warning("ollama probe unexpected status=%d url=%s", resp.status, url)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        _log_probe_failure(
            required=required,
            message=f"ollama probe failed url={url} error={exc}",
        )
    return False


def _log_probe_failure(*, required: bool, message: str) -> None:
    if required:
        logger.error(
            "%s; set OLLAMA_REQUIRED=0 to allow rule-based fallback and suppress this error",
            message,
        )
        return
    logger.warning("%s; falling back to rule-based logic for all requests", message)
