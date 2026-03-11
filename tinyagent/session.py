from __future__ import annotations

from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from .config import AppConfig, RuntimeOptions
from .conversation import ConversationMemory
from .debug import DebugLogger, DebugRequestContext
from .status import SessionStatus
from .terminal import TerminalUI
from .utils import get_content_text, get_reasoning_text


class ChatSession:
    def __init__(
        self,
        client: AsyncOpenAI,
        config: AppConfig,
        runtime_options: RuntimeOptions,
        debug_logger: DebugLogger,
    ) -> None:
        self.client = client
        self.config = config
        self.runtime_options = runtime_options
        self.debug_logger = debug_logger
        self.current_model = config.openai.model
        self.memory = ConversationMemory(config.chat)
        self.ui = TerminalUI(self.build_footer)
        self.processing = False
        self.status = SessionStatus()

    async def run(self) -> None:
        self.ui.write_line(f"Model: {self.current_model}")
        self.ui.write_line("Commands: /quit, /model, /model <name>")
        if self.debug_logger.enabled:
            self.ui.write_line(f"Debug enabled: {self.debug_logger.session_dir}")

        if self.ui.interactive:
            result = await self.ui.run(self.process_user_input)
            if result in {"interrupt", "eof"}:
                self.ui.write_line("Bye.")
            return

        while True:
            try:
                user_input = input(">").strip()
            except (EOFError, KeyboardInterrupt):
                self.ui.write_line("Bye.")
                return

            if not user_input:
                continue

            should_exit = await self.process_user_input(user_input)
            if should_exit:
                return

    async def process_user_input(self, user_input: str) -> bool:
        if self.processing:
            self.ui.write_line("A request is already running.")
            return False

        self.processing = True
        try:
            if self.ui.interactive:
                self.ui.write_line(f">{user_input}")

            if user_input.startswith("/"):
                return self.handle_command(user_input)

            compression_info = await self.memory.ensure_context_limit(
                next_user_message=user_input,
                client=self.client,
                current_model=self.current_model,
                set_status=self.set_status,
                clear_status=self.clear_status,
                write_line=self.ui.write_line,
            )
            self.memory.add_user_message(user_input)
            self.set_status("Thinking")
            self.ui.write_line("Assistant is thinking...")
            request_payload = self.build_request(stream=self.config.chat.streaming)
            debug_context = self.debug_logger.start_request(
                request_payload=request_payload,
                context_tokens_before=self.memory.total_tokens(),
                message_count=len(self.memory.messages),
                compression_info=compression_info,
            )

            try:
                assistant_text = await self.generate_reply(debug_context)
            except Exception as exc:
                self.memory.pop_last_message()
                self.clear_status()
                self.debug_logger.finalize_error(debug_context, exc)
                self.ui.write_line(f"Request failed: {exc}")
                return False

            self.memory.add_assistant_message(assistant_text)
            self.clear_status()
            return False
        finally:
            self.processing = False

    def handle_command(self, command: str) -> bool:
        raw = command.split(maxsplit=1)
        name = raw[0].lower()

        if name in {"/quit", "/exit"}:
            self.ui.write_line("Bye.")
            self.ui.exit(result="quit")
            return True

        if name == "/model":
            if len(raw) == 1:
                self.ui.write_line(f"Current model: {self.current_model}")
            else:
                new_model = raw[1].strip()
                if not new_model:
                    self.ui.write_line("Model name cannot be empty.")
                    return False
                self.current_model = new_model
                self.ui.write_line(f"Switched model to: {self.current_model}")
            return False

        self.ui.write_line(f"Unknown command: {command}")
        return False

    async def generate_reply(self, debug_context: DebugRequestContext | None) -> str:
        if self.config.chat.streaming:
            return await self.generate_reply_streaming(debug_context)
        return await self.generate_reply_once(debug_context)

    def build_footer(self) -> str:
        used_tokens = self.memory.total_tokens()
        limit = self.config.chat.context_limit_tokens
        left_ratio = max(0.0, 1 - (used_tokens / limit if limit else 1))
        left_percent = int(round(left_ratio * 100))
        return (
            f"<{self.current_model}> <{left_percent}%> left"
            f" | {self.status.format()}"
            f" | {Path.cwd().resolve()}"
        )

    def build_request(self, stream: bool) -> dict[str, Any]:
        return {
            "model": self.current_model,
            "messages": self.memory.messages,
            "stream": stream,
        }

    async def generate_reply_streaming(
        self, debug_context: DebugRequestContext | None
    ) -> str:
        stream = await self.client.chat.completions.create(
            **self.build_request(stream=True)
        )

        reasoning_buffer: list[str] = []
        answer_buffer: list[str] = []
        current_block: str | None = None

        async for chunk in stream:
            self.debug_logger.log_stream_chunk(debug_context, chunk)
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            reasoning_piece = get_reasoning_text(delta)
            answer_piece = get_content_text(delta)

            if reasoning_piece:
                reasoning_buffer.append(reasoning_piece)
                if self.config.chat.show_reasoning:
                    if current_block != "reasoning":
                        if current_block is not None:
                            self.ui.end_live_block()
                        self.ui.start_live_block("Reasoning>")
                        current_block = "reasoning"
                    self.ui.append_live_text(reasoning_piece)

            if answer_piece:
                answer_buffer.append(answer_piece)
                if current_block != "answer":
                    self.set_status("Answering")
                if self.config.chat.show_answer:
                    if current_block != "answer":
                        if current_block is not None:
                            self.ui.end_live_block()
                        self.ui.start_live_block("Answer>")
                        current_block = "answer"
                    self.ui.append_live_text(answer_piece)

        if current_block is not None:
            self.ui.end_live_block()

        final_answer = "".join(answer_buffer).strip()
        final_reasoning = "".join(reasoning_buffer).strip()
        self.debug_logger.finalize_response(
            debug_context,
            final_answer=final_answer,
            final_reasoning=final_reasoning,
        )
        return final_answer or final_reasoning

    async def generate_reply_once(
        self, debug_context: DebugRequestContext | None
    ) -> str:
        response = await self.client.chat.completions.create(
            **self.build_request(stream=False)
        )
        message = response.choices[0].message
        reasoning_text = get_reasoning_text(message)
        answer_text = get_content_text(message)

        self.set_status("Answering")
        if reasoning_text and self.config.chat.show_reasoning:
            self.ui.write_line("Reasoning>")
            self.ui.write_line(reasoning_text)
        if answer_text and self.config.chat.show_answer:
            self.ui.write_line("Answer>")
            self.ui.write_line(answer_text)

        final_text = answer_text.strip() or reasoning_text.strip()
        if not final_text:
            raise RuntimeError("The model returned an empty response.")
        self.debug_logger.finalize_response(
            debug_context,
            response=response,
            final_answer=answer_text.strip(),
            final_reasoning=reasoning_text.strip(),
        )
        return final_text

    def set_status(self, status: str) -> None:
        self.status.set(status)
        if self.ui.app:
            self.ui.app.invalidate()

    def clear_status(self) -> None:
        self.status.clear()
        if self.ui.app:
            self.ui.app.invalidate()
