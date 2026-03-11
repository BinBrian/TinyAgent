from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any


def as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in {"authorization", "proxy-authorization", "x-api-key"}:
            sanitized[key] = mask_secret(value)
        else:
            sanitized[key] = value
    return sanitized


def to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump(mode="json", exclude_none=False))
    if hasattr(value, "to_dict"):
        return to_jsonable(value.to_dict())
    if hasattr(value, "__dict__"):
        return to_jsonable(vars(value))
    return str(value)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(to_jsonable(payload), file, ensure_ascii=False, indent=2)


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(to_jsonable(payload), ensure_ascii=False))
        file.write("\n")


def extract_provider_features(payload: dict[str, Any]) -> dict[str, Any]:
    features: dict[str, Any] = {}
    for key in [
        "id",
        "object",
        "created",
        "model",
        "system_fingerprint",
        "service_tier",
    ]:
        if payload.get(key) is not None:
            features[key] = payload.get(key)

    extra_keys = sorted(
        key
        for key in payload.keys()
        if key not in {"choices", "usage"} and key not in features
    )
    if extra_keys:
        features["extra_top_level_keys"] = extra_keys
    return features


def estimate_tokens(text: str) -> int:
    cjk_chars = len(re.findall(r"[\u3400-\u9FFF]", text))
    latin_words = len(re.findall(r"[A-Za-z0-9_]+", text))
    other_chars = max(0, len(text) - cjk_chars - latin_words)
    return max(1, cjk_chars + latin_words + (other_chars // 4))


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type in {"text", "output_text", "reasoning", "summary_text"}:
                parts.append(str(item.get("text", "")))
            elif "text" in item:
                parts.append(str(item["text"]))
        return "".join(parts)
    if isinstance(value, dict):
        if "text" in value:
            return str(value["text"])
        if "content" in value:
            return normalize_text(value["content"])
    return str(value)


def get_reasoning_text(payload: Any) -> str:
    if payload is None:
        return ""
    direct = normalize_text(getattr(payload, "reasoning_content", None))
    if direct:
        return direct

    reasoning = getattr(payload, "reasoning", None)
    if reasoning is None and isinstance(payload, dict):
        reasoning = payload.get("reasoning")

    if isinstance(reasoning, str):
        return reasoning
    if isinstance(reasoning, list):
        parts: list[str] = []
        for item in reasoning:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            summary = item.get("summary")
            if isinstance(summary, list):
                parts.extend(normalize_text(block) for block in summary)
            elif summary:
                parts.append(normalize_text(summary))
            elif "text" in item:
                parts.append(str(item["text"]))
        return "".join(parts)
    if isinstance(reasoning, dict):
        return normalize_text(reasoning)
    return ""


def get_content_text(payload: Any) -> str:
    if payload is None:
        return ""
    if hasattr(payload, "content"):
        return normalize_text(getattr(payload, "content"))
    if isinstance(payload, dict):
        return normalize_text(payload.get("content"))
    return normalize_text(payload)


def get_clipboard_text() -> str:
    if sys.platform == "win32":
        try:
            import ctypes

            cf_unicode_text = 13
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            if not user32.OpenClipboard(0):
                return ""
            try:
                handle = user32.GetClipboardData(cf_unicode_text)
                if not handle:
                    return ""
                locked = kernel32.GlobalLock(handle)
                if not locked:
                    return ""
                try:
                    return ctypes.wstring_at(locked)
                finally:
                    kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
        except Exception:
            return ""

    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        try:
            return root.clipboard_get()
        finally:
            root.destroy()
    except Exception:
        return ""


def set_clipboard_text(text: str) -> bool:
    if text is None:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            gmem_moveable = 0x0002
            cf_unicode_text = 13
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32
            data = text.replace("\n", "\r\n")
            data_size = (len(data) + 1) * ctypes.sizeof(ctypes.c_wchar)
            handle = kernel32.GlobalAlloc(gmem_moveable, data_size)
            if not handle:
                return False
            locked = kernel32.GlobalLock(handle)
            if not locked:
                kernel32.GlobalFree(handle)
                return False
            try:
                ctypes.memmove(locked, ctypes.create_unicode_buffer(data), data_size)
            finally:
                kernel32.GlobalUnlock(handle)

            if not user32.OpenClipboard(0):
                kernel32.GlobalFree(handle)
                return False
            try:
                user32.EmptyClipboard()
                if not user32.SetClipboardData(cf_unicode_text, handle):
                    kernel32.GlobalFree(handle)
                    return False
                handle = None
                return True
            finally:
                user32.CloseClipboard()
        except Exception:
            return False

    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        try:
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
            return True
        finally:
            root.destroy()
    except Exception:
        return False
