"""
Unit tests for backends/*.py — call() and test_connection() functions.

All HTTP calls are mocked — no live services required.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

import backends.bing as bing
import backends.google as google
import backends.libretranslate as libre
import backends.ollama as ollama


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status_code=200, json_data=None, raise_for_status=None):
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if raise_for_status:
        resp.raise_for_status.side_effect = raise_for_status
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# Google backend
# ---------------------------------------------------------------------------

class TestGoogleCall:
    def test_success_extracts_translation(self):
        # Google returns nested list: [[["translated text", ...]]]
        fake_resp = _mock_response(json_data=[[["hallo wereld", "hello world", None]]])
        with patch("httpx.get", return_value=fake_resp) as mock_get:
            result = google.call("hello world", "en", "nl")
        assert result == "hallo wereld"
        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["sl"] == "en"
        assert kwargs["params"]["tl"] == "nl"
        assert kwargs["params"]["q"] == "hello world"

    def test_http_error_propagates(self):
        fake_resp = _mock_response(
            status_code=429,
            raise_for_status=httpx.HTTPStatusError("rate limited", request=MagicMock(), response=MagicMock()),
        )
        with patch("httpx.get", return_value=fake_resp):
            with pytest.raises(httpx.HTTPStatusError):
                google.call("hello", "en", "nl")


# ---------------------------------------------------------------------------
# Bing backend
# ---------------------------------------------------------------------------

class TestBingCall:
    _HTML = (
        '"ig":"abc123" data-iid="translator.5023" '
        'params_AbusePreventionHelper = [12345,"token-value",'
    )

    def _client(self, post_response):
        page = _mock_response()
        page.url = "https://www.bing.com/translator"
        page.text = self._HTML
        client = MagicMock()
        client.get.return_value = page
        client.post.return_value = post_response
        manager = MagicMock()
        manager.__enter__.return_value = client
        manager.__exit__.return_value = False
        return manager, client

    def test_success_extracts_translation_and_normalizes_languages(self):
        result_response = _mock_response(
            json_data=[{"translations": [{"text": "你好"}]}]
        )
        manager, client = self._client(result_response)
        with patch("backends.bing.httpx.Client", return_value=manager):
            result = bing.call("hello", "en", "zh")
        assert result == "你好"
        payload = client.post.call_args.kwargs["data"]
        assert payload["fromLang"] == "en"
        assert payload["to"] == "zh-Hans"
        assert payload["text"] == "hello"

    def test_text_is_limited_to_bing_maximum(self):
        result_response = _mock_response(
            json_data=[{"translations": [{"text": "ok"}]}]
        )
        manager, client = self._client(result_response)
        with patch("backends.bing.httpx.Client", return_value=manager):
            bing.call("x" * 1200, "auto", "zh-tw")
        payload = client.post.call_args.kwargs["data"]
        assert len(payload["text"]) == 1000
        assert payload["fromLang"] == "en"
        assert payload["to"] == "zh-Hant"

    def test_retries_when_bing_request_fails(self):
        failed = _mock_response(status_code=503)
        failed.raise_for_status.side_effect = httpx.HTTPStatusError(
            "unavailable", request=MagicMock(), response=failed
        )
        ok = _mock_response(json_data=[{"translations": [{"text": "ok"}]}])
        manager, client = self._client(ok)
        # Supply a valid discovery page for every attempt; the POST fails first.
        page = _mock_response()
        page.url = "https://www.bing.com/translator"
        page.text = self._HTML
        client.get.side_effect = [page, page, page, page, page]
        client.post.side_effect = [failed, failed, failed, failed, ok]
        with patch("backends.bing.httpx.Client", return_value=manager), patch("time.sleep"):
            assert bing.call("hello", "en", "nl") == "ok"
        assert client.post.call_count == 5


class TestBingConnection:
    def test_connection_success(self):
        manager = MagicMock()
        manager.__enter__.return_value = MagicMock()
        manager.__enter__.return_value.get.return_value = _mock_response()
        manager.__exit__.return_value = False
        with patch("backends.bing.httpx.Client", return_value=manager):
            result = bing.test_connection()
        assert "✓" in result


# ---------------------------------------------------------------------------
# LibreTranslate backend
# ---------------------------------------------------------------------------

class TestLibreTranslateCall:
    def test_success(self):
        fake_resp = _mock_response(json_data={"translatedText": "hallo"})
        with patch("httpx.post", return_value=fake_resp):
            result = libre.call("hello", "en", "nl", "http://localhost:5000", key="")
        assert result == "hallo"

    def test_api_key_included_in_payload(self):
        fake_resp = _mock_response(json_data={"translatedText": "hallo"})
        with patch("httpx.post", return_value=fake_resp) as mock_post:
            libre.call("hello", "en", "nl", "http://localhost:5000", key="mykey")
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["api_key"] == "mykey"

    def test_no_api_key_omitted_from_payload(self):
        fake_resp = _mock_response(json_data={"translatedText": "hallo"})
        with patch("httpx.post", return_value=fake_resp) as mock_post:
            libre.call("hello", "en", "nl", "http://localhost:5000", key="")
        _, kwargs = mock_post.call_args
        assert "api_key" not in kwargs["json"]

    def test_url_trailing_slash_stripped(self):
        fake_resp = _mock_response(json_data={"translatedText": "hallo"})
        with patch("httpx.post", return_value=fake_resp) as mock_post:
            libre.call("hello", "en", "nl", "http://localhost:5000/", key="")
        url = mock_post.call_args[0][0]
        assert url == "http://localhost:5000/translate"

    def test_http_error_propagates(self):
        fake_resp = _mock_response(
            status_code=500,
            raise_for_status=httpx.HTTPStatusError("server error", request=MagicMock(), response=MagicMock()),
        )
        with patch("httpx.post", return_value=fake_resp):
            with pytest.raises(httpx.HTTPStatusError):
                libre.call("hello", "en", "nl", "http://localhost:5000", key="")

    def test_rate_limit_raises_after_all_retries(self):
        """Exhausting all 429 retries raises RateLimitError (not HTTPStatusError)."""
        from exceptions import RateLimitError
        fake_resp_429 = _mock_response(status_code=429)
        with patch("httpx.post", return_value=fake_resp_429):
            with patch("time.sleep"):  # skip actual retry delays
                with pytest.raises(RateLimitError):
                    libre.call("hello", "en", "nl", "http://localhost:5000", key="")

    def test_retries_on_429_then_succeeds(self):
        """Returns translated text if a retry succeeds after a 429."""
        resp_429 = _mock_response(status_code=429)
        resp_ok  = _mock_response(json_data={"translatedText": "hallo"})
        with patch("httpx.post", side_effect=[resp_429, resp_ok]):
            with patch("time.sleep"):
                result = libre.call("hello", "en", "nl", "http://localhost:5000", key="")
        assert result == "hallo"


class TestLibreTranslateConnection:
    def test_connection_success(self):
        fake_resp = _mock_response(json_data=[{"code": "en"}, {"code": "nl"}])
        with patch("httpx.get", return_value=fake_resp):
            result = libre.test_connection("http://localhost:5000")
        assert "✓" in result
        assert "2" in result  # 2 language(s)

    def test_connection_refused(self):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            result = libre.test_connection("http://localhost:5000")
        assert "✗" in result
        assert "refused" in result.lower() or "Connection" in result

    def test_connection_timeout(self):
        with patch("httpx.get", side_effect=httpx.TimeoutException("timed out")):
            result = libre.test_connection("http://localhost:5000")
        assert "✗" in result
        assert "Timed out" in result or "timed" in result.lower()


# ---------------------------------------------------------------------------
# Ollama backend
# ---------------------------------------------------------------------------

class TestOllamaCall:
    _URL   = "http://localhost:11434"
    _MODEL = "translategemma:latest"
    _PROMPT = "Translate {source_lang} to {target_lang}: {text}"

    def test_success_extracts_content(self):
        fake_resp = _mock_response(
            json_data={"message": {"content": "hallo wereld"}}
        )
        with patch("httpx.post", return_value=fake_resp):
            result = ollama.call(
                "hello world", "en", "nl",
                self._URL, self._MODEL, self._PROMPT, key="",
            )
        assert result == "hallo wereld"

    def test_prompt_substitution(self):
        fake_resp = _mock_response(json_data={"message": {"content": "ok"}})
        with patch("httpx.post", return_value=fake_resp) as mock_post:
            ollama.call("my text", "en", "nl", self._URL, self._MODEL, self._PROMPT, key="")
        _, kwargs = mock_post.call_args
        sent_prompt = kwargs["json"]["messages"][0]["content"]
        assert "en" in sent_prompt
        assert "nl" in sent_prompt
        assert "my text" in sent_prompt
        # Template placeholders must be gone
        assert "{source_lang}" not in sent_prompt
        assert "{target_lang}" not in sent_prompt
        assert "{text}" not in sent_prompt

    def test_prompt_safe_with_curly_braces_in_text(self):
        """PDF text can contain { } — .replace() must not raise."""
        fake_resp = _mock_response(json_data={"message": {"content": "ok"}})
        with patch("httpx.post", return_value=fake_resp):
            result = ollama.call(
                "Result: {value}", "en", "nl",
                self._URL, self._MODEL, self._PROMPT, key="",
            )
        assert result == "ok"

    def test_auth_header_included_when_key_set(self):
        fake_resp = _mock_response(json_data={"message": {"content": "ok"}})
        with patch("httpx.post", return_value=fake_resp) as mock_post:
            ollama.call("text", "en", "nl", self._URL, self._MODEL, self._PROMPT, key="secret")
        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer secret"

    def test_no_auth_header_without_key(self):
        fake_resp = _mock_response(json_data={"message": {"content": "ok"}})
        with patch("httpx.post", return_value=fake_resp) as mock_post:
            ollama.call("text", "en", "nl", self._URL, self._MODEL, self._PROMPT, key="")
        _, kwargs = mock_post.call_args
        assert "Authorization" not in kwargs["headers"]

    def test_stream_false_in_payload(self):
        fake_resp = _mock_response(json_data={"message": {"content": "ok"}})
        with patch("httpx.post", return_value=fake_resp) as mock_post:
            ollama.call("text", "en", "nl", self._URL, self._MODEL, self._PROMPT, key="")
        _, kwargs = mock_post.call_args
        assert kwargs["json"]["stream"] is False

    def test_http_error_propagates(self):
        fake_resp = _mock_response(
            status_code=500,
            raise_for_status=httpx.HTTPStatusError("error", request=MagicMock(), response=MagicMock()),
        )
        with patch("httpx.post", return_value=fake_resp):
            with pytest.raises(httpx.HTTPStatusError):
                ollama.call("text", "en", "nl", self._URL, self._MODEL, self._PROMPT, key="")


class TestOllamaConnection:
    def test_connection_success(self):
        fake_resp = _mock_response(
            json_data={"models": [{"name": "translategemma:latest"}, {"name": "llama3:latest"}]}
        )
        with patch("httpx.get", return_value=fake_resp):
            result = ollama.test_connection("http://localhost:11434", key="")
        assert "✓" in result
        assert "2" in result

    def test_connection_refused(self):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            result = ollama.test_connection("http://localhost:11434", key="")
        assert "✗" in result

    def test_connection_timeout(self):
        with patch("httpx.get", side_effect=httpx.TimeoutException("timed out")):
            result = ollama.test_connection("http://localhost:11434", key="")
        assert "✗" in result
        assert "Timed out" in result or "timed" in result.lower()
