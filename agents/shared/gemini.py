"""
agents/shared/gemini.py

Shared Gemini API helpers used by all analyzer modules.

_gemini_generate() wraps generate_content with retry logic for
503 ServerError (Gemini overload / transient unavailability):
  - Retries up to 3 times
  - Waits 30 seconds between attempts
  - Raises RuntimeError if all retries are exhausted
"""
from __future__ import annotations

import time

from google.api_core.exceptions import ServiceUnavailable
from google.genai import types


_MAX_RETRIES = 3
_RETRY_WAIT  = 30   # seconds between retries


def _gemini_generate(client, model: str, contents, config: types.GenerateContentConfig):
    """
    Call client.models.generate_content() with retry on 503 ServiceUnavailable.

    Args:
        client:   genai.Client instance
        model:    model name string (e.g. "gemini-2.5-flash")
        contents: user message string or list passed to generate_content
        config:   GenerateContentConfig

    Returns:
        generate_content response object

    Raises:
        RuntimeError: if all retries fail
    """
    last_exc = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            # Match 503 / ServiceUnavailable from both google-api-core and
            # google-genai SDK which may wrap it as a generic ServerError
            err_str = str(exc).lower()
            is_503  = (
                isinstance(exc, ServiceUnavailable)
                or "503" in err_str
                or "unavailable" in err_str
                or "service unavailable" in err_str
            )

            if is_503 and attempt < _MAX_RETRIES:
                print(
                    f"  Gemini 503 on attempt {attempt}/{_MAX_RETRIES} — "
                    f"retrying in {_RETRY_WAIT}s..."
                )
                time.sleep(_RETRY_WAIT)
                last_exc = exc
                continue

            # Non-503 error or final attempt — re-raise immediately
            if is_503:
                raise RuntimeError(
                    f"Gemini API unavailable after {_MAX_RETRIES} retries — "
                    f"skipping this run"
                ) from exc
            raise

    raise RuntimeError(
        f"Gemini API unavailable after {_MAX_RETRIES} retries — skipping this run"
    ) from last_exc
