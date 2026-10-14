"""Gemini adapter behind ModelPort. The only module that imports langchain-google-genai.

The key is read from the environment by the caller and passed in; it is never logged or
placed in an exception message (only its length is reported).
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ValidationError

from factory.config import ModelConfig
from factory.ports.models import ModelRequest, ModelResponse
from factory.state import ReviewFindings

log = logging.getLogger(__name__)

# Schemas the model may be asked to fill (request.schema_name).
SCHEMAS: dict[str, type[BaseModel]] = {"ReviewFindings": ReviewFindings}

ChatFactory = Callable[[str, float], BaseChatModel]


class ModelCallError(RuntimeError):
    """Final failure; the message carries the full cause chain."""


def cause_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        chain.append(cur)
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return chain


def _status(exc: BaseException) -> int | None:
    for attr in ("status_code", "code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else None


def is_transient(exc: BaseException) -> bool:
    """Classify by exception type and cause chain, never by message text."""
    for e in cause_chain(exc):
        if isinstance(e, TimeoutError | ConnectionError):
            return True
        status = _status(e)
        if status is not None and (status == 429 or 500 <= status < 600):
            return True
        if type(e).__module__.split(".")[0] in {"httpx", "httpcore", "aiohttp", "urllib3"}:
            if "Timeout" in type(e).__name__ or "Connect" in type(e).__name__:
                return True
    return False


def retry_after_seconds(exc: BaseException) -> float | None:
    for e in cause_chain(exc):
        headers = getattr(getattr(e, "response", None), "headers", None)
        if headers is None:
            continue
        raw = headers.get("Retry-After")
        if raw is None:
            continue
        try:
            return max(0.0, float(raw))
        except ValueError:
            try:
                return max(0.0, parsedate_to_datetime(raw).timestamp() - time.time())
            except (TypeError, ValueError):
                return None
    return None


def default_chat_factory(api_key: str, base_url: str | None = None) -> ChatFactory:
    from langchain_google_genai import ChatGoogleGenerativeAI

    log.info("gemini key configured, length=%d", len(api_key))

    def make(model_id: str, temperature: float) -> BaseChatModel:
        return ChatGoogleGenerativeAI(
            model=model_id,
            temperature=temperature,
            google_api_key=api_key,
            base_url=base_url,
            max_retries=0,  # retries are ours, so they are classified and logged
            timeout=120,
        )

    return make


class GeminiModel:
    def __init__(
        self,
        config: ModelConfig,
        chat_factory: ChatFactory,
        *,
        max_attempts: int = 4,
        base_delay_s: float = 1.0,
        max_delay_s: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.config = config
        self.chat_factory = chat_factory
        self.max_attempts = max_attempts
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self.sleep = sleep
        self.rng = rng or random.Random()

    def _delay(self, attempt: int, exc: BaseException) -> float:
        hinted = retry_after_seconds(exc)
        if hinted is not None:
            return min(hinted, self.max_delay_s)
        ceiling = min(self.max_delay_s, self.base_delay_s * 2**attempt)
        return self.rng.uniform(0, ceiling)  # full jitter

    def _once(self, request: ModelRequest, model_id: str) -> tuple[str, int, int]:
        chat = self.chat_factory(model_id, min(request.temperature, self.config.temperature))
        if request.schema_name:
            schema = SCHEMAS[request.schema_name]
            parsed = cast(
                dict[str, Any],
                chat.with_structured_output(schema, include_raw=True).invoke(request.prompt),
            )
            raw, obj = parsed["raw"], parsed.get("parsed")
            if not isinstance(obj, schema):
                raise SchemaMiss(str(parsed.get("parsing_error")))
            text = obj.model_dump_json()
        else:
            raw = chat.invoke(request.prompt)
            text = raw.text if hasattr(raw, "text") and not callable(raw.text) else str(raw.content)
        usage: dict[str, Any] = getattr(raw, "usage_metadata", None) or {}
        return text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))

    def invoke(self, request: ModelRequest) -> ModelResponse:
        model_id = self.config.model_id(request.tier)
        last: BaseException | None = None
        for attempt in range(self.max_attempts):
            try:
                text, tin, tout = self._once(request, model_id)
                return ModelResponse(
                    text=text,
                    model_id=model_id,
                    prompt_hash=hashlib.sha256(request.prompt.encode()).hexdigest(),
                    input_tokens=tin,
                    output_tokens=tout,
                )
            except (SchemaMiss, ValidationError) as e:  # reject and retry, no delay
                last = e
            except Exception as e:
                if not is_transient(e):
                    raise ModelCallError(self._describe(e, attempt + 1)) from e
                last = e
                if attempt + 1 < self.max_attempts:
                    self.sleep(self._delay(attempt, e))
        assert last is not None
        raise ModelCallError(self._describe(last, self.max_attempts)) from last

    @staticmethod
    def _describe(exc: BaseException, attempts: int) -> str:
        chain = " <- ".join(f"{type(e).__name__}({_status(e)})" for e in cause_chain(exc))
        return f"model call failed after {attempts} attempt(s): {chain}"


class SchemaMiss(Exception):
    pass
