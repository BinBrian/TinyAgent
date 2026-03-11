from __future__ import annotations

import asyncio
import sys

from openai import AsyncOpenAI

from .config import (
    CONFIG_PATH,
    OpenAIConfig,
    RuntimeOptions,
    load_yaml_config,
    parse_config,
    parse_runtime_options,
)
from .debug import DebugLogger
from .session import ChatSession


def build_client(config: OpenAIConfig) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout,
        default_headers=config.extra_headers,
    )


async def run_app(runtime_options: RuntimeOptions) -> int:
    client: AsyncOpenAI | None = None
    try:
        config = parse_config(load_yaml_config(CONFIG_PATH))
        client = build_client(config.openai)
        debug_logger = DebugLogger(runtime_options.debug, config.openai)
        session = ChatSession(client, config, runtime_options, debug_logger)
        await session.run()
        return 0
    except Exception as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            await client.close()


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    runtime_options = parse_runtime_options(sys.argv[1:] if argv is None else argv)
    return asyncio.run(run_app(runtime_options))
