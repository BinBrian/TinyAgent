from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
from pathlib import Path
import time
from typing import Any
import uuid

from .config import OpenAIConfig
from .utils import (
    append_jsonl,
    extract_provider_features,
    mask_secret,
    sanitize_headers,
    to_jsonable,
    write_json,
)


@dataclass
class DebugRequestContext:
    request_id: str
    round_dir: Path
    started_at: str
    started_monotonic: float
    request_payload: dict[str, Any]
    stream: bool
    context_tokens_before: int
    message_count: int
    compression_info: dict[str, Any]
    chunk_count: int = 0
    first_chunk_ms: float | None = None
    completion_id: str | None = None
    finish_reasons: list[str] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    provider_features: dict[str, Any] = field(default_factory=dict)


class DebugLogger:
    def __init__(
        self,
        enabled: bool,
        config: OpenAIConfig | None = None,
        root_dir: Path | None = None,
    ) -> None:
        self.enabled = enabled
        self.config = config
        self.root_dir = (root_dir or Path("temp") / "logs").resolve()
        self.session_id = uuid.uuid4().hex
        self.session_md5 = hashlib.md5(self.session_id.encode("utf-8")).hexdigest()
        self.started_at = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.started_at_iso = datetime.now().isoformat(timespec="seconds")
        self.session_dir = self.root_dir / f".{self.session_md5}-{self.started_at}"
        self.rounds_dir = self.session_dir / "rounds"
        self.requests_file = self.session_dir / "requests.jsonl"
        self.request_counter = 0

        if self.enabled:
            self.rounds_dir.mkdir(parents=True, exist_ok=True)
            self.write_session_file()

    def write_session_file(self) -> None:
        if not self.enabled:
            return
        payload = {
            "session_id": self.session_id,
            "session_md5": self.session_md5,
            "started_at": self.started_at_iso,
            "cwd": str(Path.cwd().resolve()),
            "log_dir": str(self.session_dir),
            "client": {
                "base_url": self.config.base_url if self.config else "",
                "timeout": self.config.timeout if self.config else None,
                "model": self.config.model if self.config else "",
                "extra_headers": sanitize_headers(
                    self.config.extra_headers if self.config else {}
                ),
                "api_key_masked": mask_secret(self.config.api_key if self.config else ""),
            },
        }
        write_json(self.session_dir / "session.json", payload)

    def start_request(
        self,
        *,
        request_payload: dict[str, Any],
        context_tokens_before: int,
        message_count: int,
        compression_info: dict[str, Any],
    ) -> DebugRequestContext | None:
        if not self.enabled:
            return None

        self.request_counter += 1
        request_id = f"req-{self.request_counter:04d}"
        round_dir = self.rounds_dir / request_id
        round_dir.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now().isoformat(timespec="seconds")
        context = DebugRequestContext(
            request_id=request_id,
            round_dir=round_dir,
            started_at=started_at,
            started_monotonic=time.monotonic(),
            request_payload=to_jsonable(request_payload),
            stream=bool(request_payload.get("stream")),
            context_tokens_before=context_tokens_before,
            message_count=message_count,
            compression_info=compression_info,
        )
        write_json(
            round_dir / "request.json",
            {
                "request_id": request_id,
                "started_at": started_at,
                "cwd": str(Path.cwd().resolve()),
                "client": {
                    "base_url": self.config.base_url if self.config else "",
                    "timeout": self.config.timeout if self.config else None,
                    "headers": sanitize_headers(
                        self.config.extra_headers if self.config else {}
                    ),
                },
                "request": context.request_payload,
                "context": {
                    "estimated_tokens_before": context_tokens_before,
                    "message_count": message_count,
                    "compression": compression_info,
                },
            },
        )
        return context

    def log_stream_chunk(self, context: DebugRequestContext | None, chunk: Any) -> None:
        if not self.enabled or context is None:
            return

        chunk_dict = to_jsonable(chunk)
        context.chunk_count += 1
        elapsed_ms = round((time.monotonic() - context.started_monotonic) * 1000, 2)
        if context.first_chunk_ms is None:
            context.first_chunk_ms = elapsed_ms

        completion_id = chunk_dict.get("id")
        if completion_id:
            context.completion_id = completion_id

        usage = chunk_dict.get("usage")
        if isinstance(usage, dict):
            context.usage = usage

        provider_features = extract_provider_features(chunk_dict)
        if provider_features:
            context.provider_features.update(provider_features)

        choices = chunk_dict.get("choices") or []
        finish_reasons = [
            choice.get("finish_reason")
            for choice in choices
            if isinstance(choice, dict) and choice.get("finish_reason")
        ]
        if finish_reasons:
            context.finish_reasons = finish_reasons

        append_jsonl(
            context.round_dir / "stream.jsonl",
            {
                "request_id": context.request_id,
                "chunk_index": context.chunk_count,
                "elapsed_ms": elapsed_ms,
                "chunk": chunk_dict,
            },
        )

    def finalize_response(
        self,
        context: DebugRequestContext | None,
        *,
        response: Any = None,
        final_answer: str = "",
        final_reasoning: str = "",
    ) -> None:
        if not self.enabled or context is None:
            return

        ended_at = datetime.now().isoformat(timespec="seconds")
        elapsed_ms = round((time.monotonic() - context.started_monotonic) * 1000, 2)
        response_dict = to_jsonable(response) if response is not None else None
        if isinstance(response_dict, dict):
            context.completion_id = context.completion_id or response_dict.get("id")
            usage = response_dict.get("usage")
            if isinstance(usage, dict):
                context.usage = usage
            provider_features = extract_provider_features(response_dict)
            if provider_features:
                context.provider_features.update(provider_features)
            choices = response_dict.get("choices") or []
            finish_reasons = [
                choice.get("finish_reason")
                for choice in choices
                if isinstance(choice, dict) and choice.get("finish_reason")
            ]
            if finish_reasons:
                context.finish_reasons = finish_reasons

        write_json(
            context.round_dir / "response.json",
            {
                "request_id": context.request_id,
                "ended_at": ended_at,
                "elapsed_ms": elapsed_ms,
                "first_chunk_ms": context.first_chunk_ms,
                "stream": context.stream,
                "completion_id": context.completion_id,
                "finish_reasons": context.finish_reasons,
                "usage": context.usage,
                "provider_features": context.provider_features,
                "final_reasoning": final_reasoning,
                "final_answer": final_answer,
                "stream_chunks_file": (
                    str(context.round_dir / "stream.jsonl") if context.stream else None
                ),
                "raw_response": response_dict,
            },
        )
        append_jsonl(
            self.requests_file,
            {
                "request_id": context.request_id,
                "started_at": context.started_at,
                "ended_at": ended_at,
                "stream": context.stream,
                "model": context.request_payload.get("model"),
                "completion_id": context.completion_id,
                "finish_reasons": context.finish_reasons,
                "usage": context.usage,
                "elapsed_ms": elapsed_ms,
                "first_chunk_ms": context.first_chunk_ms,
                "chunk_count": context.chunk_count,
                "context_tokens_before": context.context_tokens_before,
                "message_count": context.message_count,
                "compression": context.compression_info,
                "provider_features": context.provider_features,
                "status": "ok",
            },
        )

    def finalize_error(self, context: DebugRequestContext | None, error: Exception) -> None:
        if not self.enabled or context is None:
            return

        ended_at = datetime.now().isoformat(timespec="seconds")
        elapsed_ms = round((time.monotonic() - context.started_monotonic) * 1000, 2)
        write_json(
            context.round_dir / "response.json",
            {
                "request_id": context.request_id,
                "ended_at": ended_at,
                "elapsed_ms": elapsed_ms,
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
        append_jsonl(
            self.requests_file,
            {
                "request_id": context.request_id,
                "started_at": context.started_at,
                "ended_at": ended_at,
                "stream": context.stream,
                "model": context.request_payload.get("model"),
                "elapsed_ms": elapsed_ms,
                "context_tokens_before": context.context_tokens_before,
                "message_count": context.message_count,
                "compression": context.compression_info,
                "status": "error",
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )
