"""El .env no se cargaba, y cuando se cargaba llegaba con comillas.

Medido contra la API en vivo: el valor entre comillas son 55 caracteres que
empiezan por " y devuelven 400 API_KEY_INVALID; sin comillas son 53 y devuelven
200. La clave era correcta y el error decia lo contrario.
"""

from __future__ import annotations

import os

import pytest

from src.llm.env import load_env_file, parse_env_text


def test_surrounding_quotes_are_stripped():
    assert parse_env_text('GEMINI_API_KEY="AQ.abc"')["GEMINI_API_KEY"] == "AQ.abc"
    assert parse_env_text("GEMINI_API_KEY='AQ.abc'")["GEMINI_API_KEY"] == "AQ.abc"
    # Una comilla interior no es un envoltorio y no se toca.
    assert parse_env_text('K=a"b')["K"] == 'a"b'


def test_comments_blank_lines_and_export_prefix():
    text = "\n".join(
        ["# comentario", "", "export A=1", "B = 2 ", "sin_igual", "C="]
    )
    assert parse_env_text(text) == {"A": "1", "B": "2", "C": ""}


def test_values_containing_equals_survive():
    assert parse_env_text("URL=https://x/y?a=1&b=2")["URL"] == "https://x/y?a=1&b=2"


def test_load_never_overwrites_the_real_environment(tmp_path, monkeypatch):
    """Lo que el operador exporto a mano gana sobre el archivo."""

    env = tmp_path / ".env"
    env.write_text('GEMINI_API_KEY="del-archivo"\nOTRA=nueva\n', encoding="utf-8")

    monkeypatch.setenv("GEMINI_API_KEY", "del-entorno")
    monkeypatch.delenv("OTRA", raising=False)

    loaded = load_env_file(env)

    assert os.environ["GEMINI_API_KEY"] == "del-entorno"
    assert os.environ["OTRA"] == "nueva"
    assert loaded == ["OTRA"]


def test_load_returns_names_never_values(tmp_path, monkeypatch):
    """El archivo tiene credenciales: quien llama sólo recibe NOMBRES."""

    env = tmp_path / ".env"
    env.write_text('SECRETO="valor-sensible"\n', encoding="utf-8")
    monkeypatch.delenv("SECRETO", raising=False)

    loaded = load_env_file(env)

    assert loaded == ["SECRETO"]
    assert "valor-sensible" not in repr(loaded)


def test_a_missing_file_is_not_an_error(tmp_path):
    assert load_env_file(tmp_path / "no-existe.env") == []


def test_recording_client_exposes_the_inner_accounting():
    """--record y el calculo de costo eran mutuamente excluyentes.

    El cliente exterior preguntaba el consumo de tokens a un grabador que no lo
    tiene, obtenia None, y run_audit abortaba DESPUES de facturar cada llamada.
    """

    from src.llm.runtime import InstrumentedLLMClient, RecordingLLMClient

    class _Fake:
        provider = "gemini"
        model = "m"
        last_usage = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}

        def complete(self, system_prompt, user_prompt):
            return "ok"

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        inner = InstrumentedLLMClient(
            _Fake(), pricing_fn=lambda i, o: i * 0.001 + o * 0.002
        )
        recorder = RecordingLLMClient(
            inner, recording_path=Path(d) / "s.jsonl", provider="gemini", model="m"
        )

        recorder.complete("s", "u")

        # La cifra existe y se ve a traves del grabador.
        assert recorder.llm_calls == 1
        assert recorder.total_configured_cost == pytest.approx(0.2)
        assert recorder.wall_clock_seconds >= 0.0


def test_the_default_model_is_pinned_and_not_the_dead_one():
    """gemini-2.5-flash devuelve 404 'no longer available'."""

    from src.llm.runtime import DEFAULT_GEMINI_MODEL

    assert DEFAULT_GEMINI_MODEL
    assert "2.5" not in DEFAULT_GEMINI_MODEL
