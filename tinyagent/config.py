from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .utils import as_bool, as_int

CONFIG_PATH = Path("config.yaml")
DEFAULT_CONTEXT_LIMIT = 128_000


@dataclass
class OpenAIConfig:
    base_url: str
    api_key: str
    model: str
    timeout: int = 600
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class ChatConfig:
    system_prompt: str = "You are a concise and helpful assistant."
    streaming: bool = True
    show_reasoning: bool = False
    show_answer: bool = True
    context_limit_tokens: int = DEFAULT_CONTEXT_LIMIT
    enable_context_compression: bool = True
    compression_threshold: float = 0.75
    compression_keep_last_turns: int = 4
    summary_model: str | None = None


@dataclass
class AppConfig:
    openai: OpenAIConfig
    chat: ChatConfig


@dataclass
class RuntimeOptions:
    debug: bool = False


def load_yaml_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. Create it from config.yaml.example before starting the agent."
        )

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    if not isinstance(data, dict):
        raise ValueError("config.yaml must contain a YAML object at the top level.")
    return data


def parse_config(data: dict[str, Any]) -> AppConfig:
    openai_section = data.get("openai") or {}
    chat_section = data.get("chat") or {}

    if not isinstance(openai_section, dict):
        raise ValueError("The 'openai' section must be a YAML object.")
    if not isinstance(chat_section, dict):
        raise ValueError("The 'chat' section must be a YAML object.")

    extra_headers = openai_section.get("extra_headers") or {}
    if not isinstance(extra_headers, dict):
        raise ValueError("'openai.extra_headers' must be a YAML object.")

    base_url = str(openai_section.get("base_url", "")).strip()
    api_key = str(openai_section.get("api_key", "")).strip()
    model = str(openai_section.get("model", "")).strip()

    if not base_url:
        raise ValueError("'openai.base_url' is required.")
    if not api_key:
        raise ValueError("'openai.api_key' is required.")
    if not model:
        raise ValueError("'openai.model' is required.")

    chat_config = ChatConfig(
        system_prompt=str(
            chat_section.get(
                "system_prompt", "You are a concise and helpful assistant."
            )
        ),
        streaming=as_bool(chat_section.get("streaming"), True),
        show_reasoning=as_bool(chat_section.get("show_reasoning"), False),
        show_answer=as_bool(chat_section.get("show_answer"), True),
        context_limit_tokens=as_int(
            chat_section.get("context_limit_tokens"), DEFAULT_CONTEXT_LIMIT
        ),
        enable_context_compression=as_bool(
            chat_section.get("enable_context_compression"), True
        ),
        compression_keep_last_turns=max(
            1, as_int(chat_section.get("compression_keep_last_turns"), 4)
        ),
        summary_model=(
            str(chat_section.get("summary_model")).strip()
            if chat_section.get("summary_model")
            else None
        ),
    )

    threshold = chat_section.get("compression_threshold", 0.75)
    try:
        chat_config.compression_threshold = min(max(float(threshold), 0.1), 0.95)
    except (TypeError, ValueError):
        chat_config.compression_threshold = 0.75

    return AppConfig(
        openai=OpenAIConfig(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=as_int(openai_section.get("timeout"), 600),
            extra_headers={str(k): str(v) for k, v in extra_headers.items()},
        ),
        chat=chat_config,
    )


def parse_runtime_options(argv: list[str]) -> RuntimeOptions:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--debug", default="false")
    args = parser.parse_args(argv)
    return RuntimeOptions(debug=as_bool(args.debug, False))
