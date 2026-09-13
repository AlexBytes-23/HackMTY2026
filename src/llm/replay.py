from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Sequence

from src.llm.runtime import compute_request_key


class ReplayMissError(Exception):
    """Lanzada ruidosamente cuando un request no existe en el recording."""

    def __init__(self, request_key: str, message: str | None = None):
        super().__init__(
            message
            or f"Replay miss: no recorded response found for request key '{request_key}'."
        )
        self.request_key = request_key


class ReplayLLMClient:
    """
    Cliente de reproducción 100% offline.
    Implementa el protocolo LLMClient: complete(system_prompt: str, user_prompt: str) -> str.
    No contiene ninguna llamada a red ni referencia a providers reales.
    Maneja requests repetidos consumiendo respuestas en cola FIFO por request_key.
    Si la clave no existe en la grabación o se agotan las respuestas, falla con ReplayMissError.
    """

    def __init__(
        self,
        recording_path: str | Path | None = None,
        records: dict[str, Sequence[str] | str] | None = None,
        provider: str = "gemini",
        model: str | None = None,
    ):
        self.provider = provider
        self.model = model
        self.records: dict[str, deque[str]] = {}

        if records:
            for k, v in records.items():
                if isinstance(v, (list, deque, tuple)):
                    self.records[k] = deque(v)
                else:
                    self.records[k] = deque([v])

        if recording_path:
            path = Path(recording_path)
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        data = json.loads(line)
                        k = data.get("request_key")
                        resp = data.get("response_text")
                        if k and resp is not None:
                            if k not in self.records:
                                self.records[k] = deque()
                            self.records[k].append(resp)
                            if self.model is None and data.get("model"):
                                self.model = data["model"]

        if self.model is None:
            raise ValueError(
                "ReplayLLMClient requires 'model' parameter or a recording containing 'model'."
            )

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        key = compute_request_key(
            self.provider, self.model, system_prompt, user_prompt
        )
        if key not in self.records or len(self.records[key]) == 0:
            raise ReplayMissError(key)
        return self.records[key].popleft()
