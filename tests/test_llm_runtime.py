from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error
import pytest

from src.llm.replay import ReplayLLMClient, ReplayMissError
from src.llm.runtime import (
    GeminiLLMClient,
    InstrumentedLLMClient,
    RecordingLLMClient,
    compute_prompt_hash,
    compute_request_key,
)


class MockProvider:
    def __init__(
        self,
        response: str = "mock forensic response",
        usage: dict | None = None,
        error: Exception | None = None,
        provider: str = "gemini",
        model: str = "gemini-1.5-flash",
    ):
        self.response = response
        self.last_usage = usage
        self.error = error
        self.provider = provider
        self.model = model
        self.call_count = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        if self.error:
            raise self.error
        return self.response


# 1. complete devuelve texto sin alterarlo
def test_complete_returns_unaltered_text():
    expected_text = '{"decision": "investigate", "reason": "test"}'
    mock = MockProvider(response=expected_text)
    client = InstrumentedLLMClient(mock)

    res = client.complete("system prompt", "user prompt")
    assert res == expected_text


# 2. instrumentation cuenta una llamada una sola vez
def test_instrumentation_counts_once():
    mock = MockProvider()
    client = InstrumentedLLMClient(mock)

    assert client.llm_calls == 0
    assert client.successful_calls == 0
    assert client.failed_calls == 0

    client.complete("sys", "user1")
    assert client.llm_calls == 1
    assert client.successful_calls == 1
    assert client.failed_calls == 0
    assert mock.call_count == 1

    client.complete("sys", "user2")
    assert client.llm_calls == 2
    assert client.successful_calls == 2
    assert client.failed_calls == 0
    assert mock.call_count == 2


# 3. failure incrementa failed_calls y re-lanza
def test_failure_increments_failed_calls_and_reraises():
    mock = MockProvider(error=RuntimeError("Provider timeout"))
    client = InstrumentedLLMClient(mock)

    with pytest.raises(RuntimeError, match="Provider timeout"):
        client.complete("sys", "user")

    assert client.llm_calls == 1
    assert client.successful_calls == 0
    assert client.failed_calls == 1
    assert client.last_latency >= 0.0


# 4. latency >= 0
def test_latency_non_negative():
    mock = MockProvider()
    client = InstrumentedLLMClient(mock)

    client.complete("sys", "user")
    assert client.last_latency >= 0.0
    assert client.wall_clock_seconds >= 0.0


# 5. missing usage permanece None
def test_missing_usage_remains_none():
    mock = MockProvider(usage=None)
    client = InstrumentedLLMClient(mock)

    client.complete("sys", "user")
    assert client.last_usage is None
    assert client.last_configured_cost is None


# 6. request key estable
def test_request_key_stable():
    k1 = compute_request_key("gemini", "gemini-1.5-flash", "sys", "usr")
    k2 = compute_request_key("gemini", "gemini-1.5-flash", "sys", "usr")
    assert k1 == k2
    assert len(k1) == 64  # SHA-256 hex


# 7. cambio de prompt cambia key
def test_changed_prompt_changes_key():
    base = compute_request_key("gemini", "gemini-1.5-flash", "sys", "usr")
    diff_user = compute_request_key("gemini", "gemini-1.5-flash", "sys", "usr_different")
    diff_sys = compute_request_key("gemini", "gemini-1.5-flash", "sys_different", "usr")
    diff_model = compute_request_key("gemini", "gemini-2.0-flash", "sys", "usr")

    assert base != diff_user
    assert base != diff_sys
    assert base != diff_model


# 8. recording roundtrip
def test_recording_roundtrip(tmp_path: Path):
    rec_file = tmp_path / "recordings.jsonl"
    mock = MockProvider(response="recorded answer")
    recorder = RecordingLLMClient(
        inner_client=mock,
        recording_path=rec_file,
        provider="gemini",
        model="gemini-1.5-flash",
    )

    out = recorder.complete("system prompt", "user prompt")
    assert out == "recorded answer"
    assert rec_file.exists()

    replay = ReplayLLMClient(recording_path=rec_file, provider="gemini", model="gemini-1.5-flash")
    replay_out = replay.complete("system prompt", "user prompt")
    assert replay_out == "recorded answer"


# 9. replay devuelve texto exacto
def test_replay_returns_exact_text():
    key = compute_request_key("gemini", "gemini-1.5-flash", "sys", "usr")
    records = {key: "exact forensic text 123"}
    replay = ReplayLLMClient(records=records, provider="gemini", model="gemini-1.5-flash")

    assert replay.complete("sys", "usr") == "exact forensic text 123"


# 10. replay miss falla fuerte
def test_replay_miss_fails_loudly():
    replay = ReplayLLMClient(records={}, provider="gemini", model="gemini-1.5-flash")

    with pytest.raises(ReplayMissError) as exc_info:
        replay.complete("unknown sys", "unknown usr")

    assert "Replay miss" in str(exc_info.value)
    expected_key = compute_request_key("gemini", "gemini-1.5-flash", "unknown sys", "unknown usr")
    assert exc_info.value.request_key == expected_key


# 11. replay NO toca provider
def test_replay_does_not_touch_provider():
    mock = MockProvider(response="provider text")
    key = compute_request_key("gemini", "gemini-1.5-flash", "sys", "usr")
    replay = ReplayLLMClient(
        records={key: "replay text"},
        provider="gemini",
        model="gemini-1.5-flash",
    )

    res = replay.complete("sys", "usr")
    assert res == "replay text"
    assert mock.call_count == 0  # Provider jamás fue invocado


# 12. API key nunca aparece en recording
def test_api_key_never_appears_in_recording(tmp_path: Path):
    rec_file = tmp_path / "secret_check.jsonl"
    secret_key = "AIzaSyD_SECRET_KEY_NOT_TO_LEAK"

    mock = MockProvider(response="ok")
    mock.api_key = secret_key

    recorder = RecordingLLMClient(
        inner_client=mock,
        recording_path=rec_file,
        provider="gemini",
        model="gemini-1.5-flash",
    )
    recorder.complete("sys", "usr")

    content = rec_file.read_text(encoding="utf-8")
    assert secret_key not in content


# 13. prompts literales no se guardan por default
def test_literal_prompts_not_saved_by_default(tmp_path: Path):
    rec_file = tmp_path / "no_prompts.jsonl"
    sys_prompt = "SUPER_SECRET_SYSTEM_INSTRUCTIONS"
    usr_prompt = "PRIVATE_AUDIT_TRANSACTION_QUERY"

    mock = MockProvider(response="ok")
    recorder = RecordingLLMClient(
        inner_client=mock,
        recording_path=rec_file,
        provider="gemini",
        model="gemini-1.5-flash",
        save_prompts=False,
    )
    recorder.complete(sys_prompt, usr_prompt)

    line = rec_file.read_text(encoding="utf-8").strip()
    data = json.loads(line)

    assert "system_prompt" not in data
    assert "user_prompt" not in data
    assert "system_prompt_hash" in data
    assert "user_prompt_hash" in data
    assert data["system_prompt_hash"] == compute_prompt_hash(sys_prompt)
    assert data["user_prompt_hash"] == compute_prompt_hash(usr_prompt)


# 14. pricing desconocido -> None (NO se convierte a 0.0)
def test_unknown_pricing_returns_none():
    mock = MockProvider(
        usage={"input_tokens": 150, "output_tokens": 50, "total_tokens": 200}
    )
    client = InstrumentedLLMClient(mock, pricing_fn=None)
    client.complete("sys", "usr")

    assert client.last_usage is not None
    assert client.last_configured_cost is None
    assert client.last_configured_cost != 0.0


# 15. pricing inyectado funciona con usage factual
def test_injected_pricing_works_with_factual_usage():
    mock = MockProvider(
        usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
    )
    pricing_fn = lambda in_tok, out_tok: (in_tok * 0.001) + (out_tok * 0.002)

    client = InstrumentedLLMClient(mock, pricing_fn=pricing_fn)
    client.complete("sys", "usr")

    # 100 * 0.001 + 50 * 0.002 = 0.1 + 0.1 = 0.2
    assert client.last_configured_cost == pytest.approx(0.2)


# ============================================================
# NUEVOS TESTS ESPECÍFICOS DE REQUISITOS ADICIONALES
# ============================================================

# Punto 1: Replay de requests repetidos (FIFO queue por request_key)
def test_replay_repeated_requests_fifo_queue(tmp_path: Path):
    rec_file = tmp_path / "repeated.jsonl"
    k = compute_request_key("gemini", "gemini-1.5-flash", "sys", "usr")

    # Escribimos dos respuestas consecutivas para el mismo prompt
    entry1 = {
        "request_key": k,
        "provider": "gemini",
        "model": "gemini-1.5-flash",
        "response_text": "Response A",
    }
    entry2 = {
        "request_key": k,
        "provider": "gemini",
        "model": "gemini-1.5-flash",
        "response_text": "Response B",
    }
    with open(rec_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(entry1) + "\n")
        f.write(json.dumps(entry2) + "\n")

    replay = ReplayLLMClient(recording_path=rec_file, provider="gemini", model="gemini-1.5-flash")

    # Primera llamada -> Response A
    assert replay.complete("sys", "usr") == "Response A"
    # Segunda llamada -> Response B
    assert replay.complete("sys", "usr") == "Response B"
    # Tercera llamada -> ReplayMissError (cola agotada)
    with pytest.raises(ReplayMissError):
        replay.complete("sys", "usr")


# Punto 2: Cost Aggregation (semántica conservadora)
def test_cost_aggregation_all_known():
    pricing_fn = lambda in_tok, out_tok: (in_tok * 0.001) + (out_tok * 0.002)
    mock = MockProvider(usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
    client = InstrumentedLLMClient(mock, pricing_fn=pricing_fn)

    client.complete("sys", "usr1")  # costo = 0.2
    client.complete("sys", "usr2")  # costo = 0.2

    assert client.successful_calls == 2
    assert client.has_unknown_cost is False
    assert client.total_configured_cost == pytest.approx(0.4)
    assert client.known_cost_sum == pytest.approx(0.4)


def test_cost_aggregation_one_known_one_unknown_marks_total_none():
    pricing_fn = lambda in_tok, out_tok: (in_tok * 0.001) + (out_tok * 0.002)
    mock = MockProvider(usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
    client = InstrumentedLLMClient(mock, pricing_fn=pricing_fn)

    # 1. Llamada conocida
    client.complete("sys", "usr1")
    assert client.total_configured_cost == pytest.approx(0.2)

    # 2. Llamada desconocida (sin usage)
    mock.last_usage = None
    client.complete("sys", "usr2")

    assert client.successful_calls == 2
    assert client.has_unknown_cost is True
    # El total acumulado DEBE ser None (no presentar suma parcial como total)
    assert client.total_configured_cost is None
    # La suma parcial conocida se conserva aparte
    assert client.known_cost_sum == pytest.approx(0.2)


# Punto 3: Gemini multi-part response (totalmente offline con mock de urllib)
def test_gemini_multi_part_response_concatenation():
    fake_payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "Part 1; "},
                        {"text": "Part 2; "},
                        {"text": "Part 3."},
                    ]
                }
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 30,
            "candidatesTokenCount": 20,
            "totalTokenCount": 50,
        },
    }
    fake_body = json.dumps(fake_payload).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fake_body
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        client = GeminiLLMClient(api_key="fake-key", model="gemini-1.5-flash")
        result = client.complete("sys prompt", "user prompt")

        assert result == "Part 1; Part 2; Part 3."
        assert client.last_usage == {
            "input_tokens": 30,
            "output_tokens": 20,
            "total_tokens": 50,
        }
        assert mock_urlopen.called


def test_gemini_multi_part_no_text_fails_loudly():
    fake_payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"thought": "some reasoning without text key"},
                    ]
                }
            }
        ]
    }
    fake_body = json.dumps(fake_payload).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fake_body
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        client = GeminiLLMClient(api_key="fake-key", model="gemini-1.5-flash")
        with pytest.raises(RuntimeError, match="contained no text parts"):
            client.complete("sys", "usr")


# Punto 4: Full wrapper composition test
def test_full_wrapper_composition(tmp_path: Path):
    rec_file = tmp_path / "composition.jsonl"
    fake_provider = MockProvider(
        response='{"result": "finding verified"}',
        usage={"input_tokens": 45, "output_tokens": 15, "total_tokens": 60},
        provider="gemini",
        model="gemini-1.5-flash",
    )
    pricing_fn = lambda in_tok, out_tok: (in_tok * 0.001) + (out_tok * 0.002)

    # FakeProvider -> InstrumentedLLMClient -> RecordingLLMClient
    instrumented = InstrumentedLLMClient(fake_provider, pricing_fn=pricing_fn)
    recording = RecordingLLMClient(
        inner_client=instrumented,
        recording_path=rec_file,
        provider="gemini",
        model="gemini-1.5-flash",
    )

    # 1. Ejecución con recording activo
    res = recording.complete("system instruction", "audit case prompt")
    assert res == '{"result": "finding verified"}'
    assert instrumented.llm_calls == 1
    assert instrumented.successful_calls == 1
    assert instrumented.total_configured_cost == pytest.approx(0.045 + 0.030)
    assert fake_provider.call_count == 1
    assert rec_file.exists()

    # 2. Reproducción offline con ReplayLLMClient
    replay = ReplayLLMClient(
        recording_path=rec_file,
        provider="gemini",
        model="gemini-1.5-flash",
    )
    replay_res = replay.complete("system instruction", "audit case prompt")

    # Verifica texto idéntico y cero llamadas adicionales al provider
    assert replay_res == '{"result": "finding verified"}'
    assert fake_provider.call_count == 1  # No fue llamado en replay


# Punto 5: Error Hygiene (API key no se fuga en excepciones)
def test_error_hygiene_sanitizes_api_key():
    secret_key = "AIzaSyD_MY_SUPER_SECRET_KEY"
    fake_http_error = urllib.error.HTTPError(
        url="http://fake.url",
        code=403,
        msg="Forbidden",
        hdrs=None,  # type: ignore
        fp=io.BytesIO(f"Error occurred with key {secret_key} unauthorized".encode("utf-8")),
    )

    with patch("urllib.request.urlopen", side_effect=fake_http_error):
        client = GeminiLLMClient(api_key=secret_key, model="gemini-1.5-flash")
        with pytest.raises(RuntimeError) as exc_info:
            client.complete("sys", "usr")

        err_msg = str(exc_info.value)
        assert secret_key not in err_msg
        assert "***" in err_msg


# Pruebas de configuración de GeminiLLMClient
def test_gemini_client_missing_config_fails_loudly(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiLLMClient(api_key=None, model="gemini-1.5-flash")

    with pytest.raises(ValueError, match="GEMINI_MODEL"):
        GeminiLLMClient(api_key="valid-key", model=None)


def test_gemini_client_explicit_config_accepted():
    client = GeminiLLMClient(api_key="test-key", model="gemini-2.0-flash")
    assert client.api_key == "test-key"
    assert client.model == "gemini-2.0-flash"
    assert client.provider == "gemini"
