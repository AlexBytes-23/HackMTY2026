from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable


def compute_prompt_hash(prompt: str) -> str:
    """Calcula el hash SHA-256 determinista de un prompt."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def compute_request_key(
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    """
    Calcula el request_key determinista usando SHA-256 sobre JSON canónico.
    No incluye timestamps ni credenciales.
    """
    canonical = {
        "model": model.strip(),
        "provider": provider.strip().lower(),
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
    }
    raw = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class GeminiLLMClient:
    """
    Cliente mínimo para Google Gemini usando la biblioteca estándar de Python (urllib).
    Implementa exactamente: complete(system_prompt: str, user_prompt: str) -> str.
    Cero llamadas de red en import time.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    ):
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise ValueError("GEMINI_API_KEY must be set in environment or passed explicitly.")

        mdl = model or os.getenv("GEMINI_MODEL")
        if not mdl:
            raise ValueError("GEMINI_MODEL must be set in environment or passed explicitly.")

        self.api_key = key
        self.model = mdl
        self.provider = "gemini"
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.last_usage: dict[str, int | None] | None = None

    def _build_payload(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Construye el cuerpo de la petición. Sin red, para poder testearlo.

        ``responseMimeType`` pide JSON al proveedor. No sustituye a
        ``strip_code_fences`` en los parsers: es una petición, no una garantía.
        """
        return {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_prompt}],
                }
            ],
            "systemInstruction": {
                "parts": [{"text": system_prompt}],
            },
            "generationConfig": {"responseMimeType": "application/json"},
        }

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url}/models/{self.model}:generateContent"
        payload = self._build_payload(system_prompt, user_prompt)
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            if self.api_key in err_body:
                err_body = err_body.replace(self.api_key, "***")
            raise RuntimeError(f"Gemini API error ({e.code}): {err_body}") from None
        except urllib.error.URLError as e:
            reason = str(e.reason)
            if self.api_key in reason:
                reason = reason.replace(self.api_key, "***")
            raise RuntimeError(f"Gemini connection error: {reason}") from None

        # Captura factual de tokens (solo si Gemini los devuelve)
        usage_meta = resp_data.get("usageMetadata")
        if isinstance(usage_meta, dict):
            self.last_usage = {
                "input_tokens": usage_meta.get("promptTokenCount"),
                "output_tokens": usage_meta.get("candidatesTokenCount"),
                "total_tokens": usage_meta.get("totalTokenCount"),
            }
        else:
            self.last_usage = None

        candidates = resp_data.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini API returned no candidates.")

        parts = candidates[0].get("content", {}).get("parts", [])
        text_parts = [
            p["text"]
            for p in parts
            if isinstance(p, dict) and "text" in p and isinstance(p["text"], str)
        ]
        if not text_parts:
            raise RuntimeError("Gemini API response contained no text parts.")

        return "".join(text_parts)


class InstrumentedLLMClient:
    """
    Wrapper de instrumentación factual con agregación conservadora de costo.
    Implementa complete(system_prompt: str, user_prompt: str) -> str sin alterar texto.
    """

    def __init__(
        self,
        inner_client: Any,
        pricing_fn: Callable[[int, int], float] | None = None,
        provider: str | None = None,
        model: str | None = None,
    ):
        self.inner_client = inner_client
        self.pricing_fn = pricing_fn
        self.provider = provider or getattr(inner_client, "provider", "unknown")
        self.model = model or getattr(inner_client, "model", "unknown")

        self.llm_calls: int = 0
        self.successful_calls: int = 0
        self.failed_calls: int = 0
        self.wall_clock_seconds: float = 0.0
        self.last_latency: float = 0.0
        self.last_usage: dict[str, int | None] | None = None
        self.last_configured_cost: float | None = None

        self.known_cost_sum: float = 0.0
        self.has_unknown_cost: bool = False

    @property
    def total_configured_cost(self) -> float | None:
        """
        Devuelve el total acumulado de costo sólo si TODAS las llamadas exitosas
        tuvieron usage y pricing calculable. Si alguna llamada no tuvo usage o pricing,
        el total se reporta estrictamente como None.
        """
        if self.has_unknown_cost:
            return None
        if self.successful_calls == 0:
            return 0.0
        return self.known_cost_sum

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.llm_calls += 1
        start = time.perf_counter()
        try:
            response_text = self.inner_client.complete(system_prompt, user_prompt)
            duration = max(0.0, time.perf_counter() - start)
            self.last_latency = duration
            self.wall_clock_seconds += duration
            self.successful_calls += 1

            # Extraer uso reportado del provider
            raw_usage = getattr(self.inner_client, "last_usage", None)
            if raw_usage and isinstance(raw_usage, dict):
                in_tok = raw_usage.get("input_tokens")
                out_tok = raw_usage.get("output_tokens")
                tot_tok = raw_usage.get("total_tokens")
                self.last_usage = {
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    "total_tokens": tot_tok,
                }
                if (
                    self.pricing_fn is not None
                    and in_tok is not None
                    and out_tok is not None
                ):
                    call_cost = self.pricing_fn(in_tok, out_tok)
                    self.last_configured_cost = call_cost
                    self.known_cost_sum += call_cost
                else:
                    self.last_configured_cost = None
                    self.has_unknown_cost = True
            else:
                self.last_usage = None
                self.last_configured_cost = None
                self.has_unknown_cost = True

            return response_text
        except Exception:
            duration = max(0.0, time.perf_counter() - start)
            self.last_latency = duration
            self.wall_clock_seconds += duration
            self.failed_calls += 1
            raise


class RecordingLLMClient:
    """
    Wrapper que graba respuestas exitosas en un archivo JSONL secuencial.
    No almacena prompts literales por defecto (solo hashes).
    No almacena secretos.
    """

    def __init__(
        self,
        inner_client: Any,
        recording_path: str | Path,
        provider: str | None = None,
        model: str | None = None,
        save_prompts: bool = False,
    ):
        self.inner_client = inner_client
        self.recording_path = Path(recording_path)
        self.provider = provider or getattr(inner_client, "provider", "unknown")
        self.model = model or getattr(inner_client, "model", "unknown")
        self.save_prompts = save_prompts
        self.recording_path.parent.mkdir(parents=True, exist_ok=True)

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response_text = self.inner_client.complete(system_prompt, user_prompt)

        req_key = compute_request_key(
            self.provider, self.model, system_prompt, user_prompt
        )
        sys_hash = compute_prompt_hash(system_prompt)
        usr_hash = compute_prompt_hash(user_prompt)

        usage = getattr(self.inner_client, "last_usage", None)
        latency = getattr(self.inner_client, "last_latency", None)
        cost = getattr(self.inner_client, "last_configured_cost", None)

        entry = {
            "request_key": req_key,
            "provider": self.provider,
            "model": self.model,
            "system_prompt_hash": sys_hash,
            "user_prompt_hash": usr_hash,
            "response_text": response_text,
            "usage": usage,
            "latency": latency,
            "configured_cost": cost,
        }
        if self.save_prompts:
            entry["system_prompt"] = system_prompt
            entry["user_prompt"] = user_prompt

        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(self.recording_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()

        return response_text
