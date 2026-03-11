from __future__ import annotations

from typing import Any, Callable

from openai import AsyncOpenAI

from .config import ChatConfig
from .utils import estimate_tokens, get_content_text


class ConversationMemory:
    def __init__(self, config: ChatConfig) -> None:
        self.config = config
        self.messages: list[dict[str, str]] = []
        self.summary_count = 0
        if config.system_prompt:
            self.messages.append({"role": "system", "content": config.system_prompt.strip()})

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str) -> None:
        self.messages.append({"role": "assistant", "content": content})

    def pop_last_message(self) -> None:
        if self.messages:
            self.messages.pop()

    def total_tokens(self, next_user_message: str = "") -> int:
        total = 0
        for message in self.messages:
            total += estimate_tokens(message.get("content", ""))
            total += 4
        if next_user_message:
            total += estimate_tokens(next_user_message) + 4
        return total

    async def ensure_context_limit(
        self,
        *,
        next_user_message: str,
        client: AsyncOpenAI,
        current_model: str,
        set_status: Callable[[str], None],
        clear_status: Callable[[], None],
        write_line: Callable[[str], None],
    ) -> dict[str, Any]:
        stats = {"compressed": False, "trimmed": False, "summary_count": 0}
        if self.total_tokens(next_user_message) <= self.config.context_limit_tokens:
            return stats
        if not self.config.enable_context_compression:
            self.trim_to_fit(next_user_message, write_line)
            stats["trimmed"] = True
            return stats

        threshold_tokens = int(
            self.config.context_limit_tokens * self.config.compression_threshold
        )
        set_status("Compressing")
        while self.total_tokens(next_user_message) > threshold_tokens:
            compressed = await self.compress_history(
                client=client,
                current_model=current_model,
                write_line=write_line,
            )
            if not compressed:
                self.trim_to_fit(next_user_message, write_line)
                clear_status()
                stats["trimmed"] = True
                return stats
            stats["compressed"] = True
            stats["summary_count"] += 1
        clear_status()
        return stats

    async def compress_history(
        self,
        *,
        client: AsyncOpenAI,
        current_model: str,
        write_line: Callable[[str], None],
    ) -> bool:
        if len(self.messages) <= 3:
            return False

        base_system_messages = [
            msg
            for msg in self.messages
            if msg["role"] == "system"
            and not msg["content"].startswith("[Conversation summary #")
        ]
        non_system_messages = [msg for msg in self.messages if msg["role"] != "system"]
        keep_pairs = self.config.compression_keep_last_turns * 2

        if len(non_system_messages) <= keep_pairs:
            return False

        keep_tail = non_system_messages[-keep_pairs:]
        to_summarize = non_system_messages[:-keep_pairs]
        if not to_summarize:
            return False

        summary_messages = [
            {
                "role": "system",
                "content": "You compress chat history into a compact factual memory.",
            },
            {
                "role": "user",
                "content": (
                    "Summarize the conversation below for future turns.\n"
                    "Keep: user goals, constraints, decisions, unresolved questions, "
                    "and any facts the assistant should preserve.\n"
                    "Use concise bullet points.\n\n"
                    "Conversation:\n"
                    + "\n".join(
                        f"{item['role'].upper()}: {item['content']}"
                        for item in to_summarize
                    )
                ),
            },
        ]

        response = await client.chat.completions.create(
            model=self.config.summary_model or current_model,
            messages=summary_messages,
            stream=False,
        )

        summary_text = get_content_text(response.choices[0].message).strip()
        if not summary_text:
            return False

        self.summary_count += 1
        self.messages = base_system_messages + [
            {
                "role": "system",
                "content": f"[Conversation summary #{self.summary_count}]\n{summary_text}",
            }
        ] + keep_tail
        write_line("[Context compressed]")
        return True

    def trim_to_fit(
        self,
        next_user_message: str,
        write_line: Callable[[str], None],
    ) -> None:
        removable_indexes = [
            index
            for index, message in enumerate(self.messages)
            if message["role"] != "system"
        ]
        while (
            removable_indexes
            and self.total_tokens(next_user_message) > self.config.context_limit_tokens
        ):
            self.messages.pop(removable_indexes.pop(0))
            removable_indexes = [
                index
                for index, message in enumerate(self.messages)
                if message["role"] != "system"
            ]
        write_line("[Old context trimmed]")
