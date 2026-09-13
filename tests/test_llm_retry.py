"""Retry behaviour: a transient API error must not destroy a case."""
from __future__ import annotations
import urllib.error
import pytest
from src.llm.runtime import GeminiLLMClient


def _client(monkeypatch, responses):
    c = GeminiLLMClient(api_key="k", model="m", max_attempts=4)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    calls = {"n": 0}

    def fake(system_prompt, user_prompt):
        i = calls["n"]; calls["n"] += 1
        r = responses[min(i, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(c, "_complete_once", fake)
    return c, calls


def _http(code, body=b"{}"):
    e = urllib.error.HTTPError("u", code, "err", {}, None)
    err = RuntimeError("Gemini API error (%d): %s" % (code, body.decode()))
    err.http_status = code
    return err


def test_rate_limit_is_retried_and_then_succeeds(monkeypatch):
    c, calls = _client(monkeypatch, [_http(429), _http(429), "ok"])
    assert c.complete("s", "u") == "ok"
    assert calls["n"] == 3


def test_timeout_is_retried(monkeypatch):
    c, calls = _client(monkeypatch, [TimeoutError("read timed out"), "ok"])
    assert c.complete("s", "u") == "ok"
    assert calls["n"] == 2


def test_a_bad_request_is_never_retried(monkeypatch):
    """A 400 is our fault. Retrying it wastes quota and hides the bug."""
    c, calls = _client(monkeypatch, [_http(400)])
    with pytest.raises(RuntimeError):
        c.complete("s", "u")
    assert calls["n"] == 1


def test_it_gives_up_and_reports_honestly(monkeypatch):
    c, calls = _client(monkeypatch, [_http(429)])
    with pytest.raises(RuntimeError, match="after 4 attempt"):
        c.complete("s", "u")
    assert calls["n"] == 4


def test_it_honours_the_delay_the_api_asks_for():
    body = '{"error":{"details":[{"retryDelay":"27.7s"}]}}'
    assert GeminiLLMClient._retry_delay_from(body, 0) == pytest.approx(28.2)
    assert GeminiLLMClient._retry_delay_from("no delay here", 2) == 8.0
