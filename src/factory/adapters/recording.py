"""Record real model responses once, replay them deterministically (SPEC section 11)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factory.ports import ModelPort
from factory.ports.models import ModelRequest, ModelResponse


class RecordingModel:
    """Wraps a real ModelPort and keeps every exchange, in call order per role."""

    def __init__(self, inner: ModelPort) -> None:
        self.inner = inner
        self.calls: list[dict[str, Any]] = []

    def invoke(self, request: ModelRequest) -> ModelResponse:
        resp = self.inner.invoke(request)
        self.calls.append(
            {
                "role": request.role,
                "tier": request.tier,
                "schema": request.schema_name,
                "prompt_hash": resp.prompt_hash,
                "model_id": resp.model_id,
                "text": resp.text,
                "input_tokens": resp.input_tokens,
                "output_tokens": resp.output_tokens,
            }
        )
        return resp

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"calls": self.calls}, indent=1, sort_keys=True))


class ReplayModel:
    """Plays recorded responses per role in order. Prompt drift (a recorded prompt hash that no
    longer matches) is counted, not fatal: it tells you the recording is stale."""

    def __init__(self, recording: Path | dict[str, Any]) -> None:
        data = json.loads(recording.read_text()) if isinstance(recording, Path) else recording
        self.queues: dict[str, list[dict[str, Any]]] = {}
        for c in data["calls"]:
            self.queues.setdefault(c["role"], []).append(c)
        self.drift = 0
        self.served = 0

    def invoke(self, request: ModelRequest) -> ModelResponse:
        q = self.queues.get(request.role)
        if not q:
            raise RuntimeError(f"recording exhausted for role {request.role!r}")
        c = q.pop(0)
        import hashlib

        ph = hashlib.sha256(request.prompt.encode()).hexdigest()
        if ph != c["prompt_hash"]:
            self.drift += 1
        self.served += 1
        return ModelResponse(
            text=c["text"],
            model_id=c["model_id"],
            prompt_hash=ph,
            input_tokens=c["input_tokens"],
            output_tokens=c["output_tokens"],
        )
