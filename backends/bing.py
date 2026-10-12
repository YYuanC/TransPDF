"""TransPDF — Bing web translator backend.

Uses the same unauthenticated web endpoint as Bing Translator's web client.
The endpoint is unofficial and may change without notice; it requires no API
key, but does require internet access and sends translated text to Bing.
"""

import logging
import re
import time

import httpx

from config import CONNECTION_TIMEOUT, TRANSLATE_TIMEOUT

logger = logging.getLogger(__name__)

_ENDPOINT = "https://www.bing.com/translator"
_MAX_TEXT_LENGTH = 1000  # Bing's web endpoint limit
_RETRY_ATTEMPTS = 5
_RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
    ),
}

# Bing uses script-specific names for Chinese and does not expose a useful
# auto-detect mode through this endpoint. This follows the reference
# implementation used by PDFMathTranslate-next.
_LANGUAGE_MAP = {
    "zh": "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh-tw": "zh-Hant",
    "zh-hk": "zh-Hant",
    "zh-hant": "zh-Hant",
    "zh-hans": "zh-Hans",
    "auto": "en",
}


def _normalize_language(language: str) -> str:
    return _LANGUAGE_MAP.get(language.lower().replace("_", "-"), language)


def _find_session_params(client: httpx.Client) -> tuple[str, str, str, str, str]:
    """Fetch Bing's translator page and extract its request parameters."""
    response = client.get(_ENDPOINT, timeout=CONNECTION_TIMEOUT)
    response.raise_for_status()

    # The page normally redirects to a locale-specific URL. Keep the origin
    # and path prefix, then append ttranslatev3 below.
    response_url = str(response.url)
    url = response_url.rsplit("translator", 1)[0]

    ig_match = re.search(r'"ig":"(.*?)"', response.text)
    iid_match = re.findall(r'data-iid="(.*?)"', response.text)
    abuse_match = re.search(
        r'params_AbusePreventionHelper\s*=\s*\[(.*?),"(.*?)",',
        response.text,
    )
    if not (ig_match and iid_match and abuse_match):
        raise ValueError("Unable to extract Bing Translator session parameters.")

    key, token = abuse_match.groups()
    return url, ig_match.group(1), iid_match[-1], key, token


def _translate_once(client: httpx.Client, text: str, source: str, target: str) -> str:
    url, ig, iid, key, token = _find_session_params(client)
    response = client.post(
        f"{url}ttranslatev3?IG={ig}&IID={iid}",
        data={
            "fromLang": _normalize_language(source),
            "to": _normalize_language(target),
            "text": text[:_MAX_TEXT_LENGTH],
            "token": token,
            "key": key,
        },
        headers=_HEADERS,
        timeout=TRANSLATE_TIMEOUT,
    )
    response.raise_for_status()
    try:
        return response.json()[0]["translations"][0]["text"]
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Unexpected response from Bing Translator.") from exc


def call(text: str, source: str, target: str) -> str:
    """Translate one text block through Bing's web translator endpoint.

    Bing's endpoint occasionally rejects requests while rotating its abuse
    prevention token, so the complete session-discovery and translation flow
    is retried up to five times with exponential backoff.
    """
    last_error: Exception | None = None
    # Follow Bing's regional redirects before extracting session parameters.
    with httpx.Client(
        headers=_HEADERS, timeout=TRANSLATE_TIMEOUT, follow_redirects=True,
    ) as client:
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                return _translate_once(client, text, source, target)
            except Exception as exc:  # Bing errors are transient in practice.
                last_error = exc
                if attempt == _RETRY_ATTEMPTS - 1:
                    raise
                delay = _RETRY_DELAYS[attempt]
                logger.warning(
                    "Bing translation attempt %d/%d failed: %s; retrying in %.1fs",
                    attempt + 1,
                    _RETRY_ATTEMPTS,
                    exc,
                    delay,
                )
                time.sleep(delay)

    # The loop always returns or raises; keep a useful type-checker fallback.
    raise RuntimeError("Bing translation failed") from last_error


def test_connection() -> str:
    """Check whether the Bing Translator web endpoint is reachable."""
    try:
        with httpx.Client(
            headers=_HEADERS, timeout=CONNECTION_TIMEOUT, follow_redirects=True,
        ) as client:
            response = client.get(_ENDPOINT, timeout=CONNECTION_TIMEOUT)
            response.raise_for_status()
        return "✓ Connected to Bing Translator"
    except httpx.ConnectError:
        return "✗ Connection refused — check your internet connection"
    except httpx.TimeoutException:
        return f"✗ Timed out after {CONNECTION_TIMEOUT}s"
    except Exception as exc:
        return f"✗ {exc}"
