from __future__ import annotations

from pathlib import Path
import sys
import time
from typing import Any, Awaitable, Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENDOR_PATH = PROJECT_ROOT / ".vendor"
if VENDOR_PATH.exists():
    sys.path.insert(0, str(VENDOR_PATH))

try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.document import Document
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.mouse_events import MouseButton, MouseEventType
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import TextArea
    from prompt_toolkit.history import InMemoryHistory
except ImportError:
    Application = None
    Document = None
    KeyBindings = None
    HSplit = None
    Layout = None
    Window = None
    FormattedTextControl = None
    MouseButton = None
    MouseEventType = None
    Style = None
    TextArea = None
    InMemoryHistory = None

from .utils import get_clipboard_text, set_clipboard_text


class TerminalUI:
    def __init__(self, footer_provider: Any) -> None:
        self.footer_provider = footer_provider
        self.interactive = bool(
            sys.stdin.isatty() and sys.stdout.isatty() and Application is not None
        )
        self.submit_handler: Callable[[str], Awaitable[None]] | None = None
        self.output_lines: list[str] = []
        self.live_line_index: int | None = None
        self.app = None
        self.output_field = None
        self.input_field = None
        self.notice_text = ""
        self.notice_expires_at = 0.0
        if self.interactive:
            try:
                self.build_application()
            except Exception:
                self.interactive = False

    def build_application(self) -> None:
        self.output_field = TextArea(
            text="",
            read_only=True,
            scrollbar=True,
            focusable=True,
            focus_on_click=True,
            wrap_lines=True,
            history=InMemoryHistory(),
        )
        self.input_field = TextArea(
            height=1,
            prompt=">",
            multiline=False,
            wrap_lines=False,
            history=InMemoryHistory(),
            accept_handler=self.accept_input,
            focus_on_click=True,
        )
        footer = Window(
            height=1,
            content=FormattedTextControl(text=self.bottom_toolbar),
            style="class:status",
        )
        self.attach_mouse_handlers()
        layout = Layout(
            HSplit([self.output_field, self.input_field, footer]),
            focused_element=self.input_field,
        )
        bindings = KeyBindings()

        @bindings.add("c-c")
        def _(event: Any) -> None:
            event.app.exit(result="interrupt")

        @bindings.add("c-d")
        def _(event: Any) -> None:
            if not self.input_field.text:
                event.app.exit(result="eof")

        @bindings.add("tab")
        def _(event: Any) -> None:
            self.toggle_focus()

        @bindings.add("escape")
        def _(event: Any) -> None:
            self.focus_input()

        @bindings.add("pageup")
        def _(event: Any) -> None:
            self.focus_output()
            self.scroll_output(lines=-10)

        @bindings.add("pagedown")
        def _(event: Any) -> None:
            self.focus_output()
            self.scroll_output(lines=10)

        @bindings.add("home")
        def _(event: Any) -> None:
            self.focus_output()
            self.scroll_output_to_edge(top=True)

        @bindings.add("end")
        def _(event: Any) -> None:
            self.focus_output()
            self.scroll_output_to_edge(top=False)

        self.app = Application(
            layout=layout,
            key_bindings=bindings,
            full_screen=True,
            mouse_support=True,
            refresh_interval=0.2,
            style=Style.from_dict(
                {
                    "status": "fg:#909090",
                    "text-area": "",
                }
            ),
        )

    def accept_input(self, buffer: Any) -> bool:
        text = buffer.text.strip()
        buffer.text = ""
        if not text or not self.submit_handler or not self.app:
            return False
        self.app.create_background_task(self.submit_handler(text))
        return False

    def write_line(self, text: str = "") -> None:
        if not self.interactive:
            print(text)
            return
        self.output_lines.extend(text.splitlines() or [""])
        self.refresh_output()

    def start_live_block(self, label: str) -> None:
        if not self.interactive:
            print(label)
            return
        self.output_lines.append(label)
        self.output_lines.append("")
        self.live_line_index = len(self.output_lines) - 1
        self.refresh_output()

    def append_live_text(self, text: str) -> None:
        if not text:
            return
        if not self.interactive:
            print(text, end="", flush=True)
            return
        if self.live_line_index is None:
            self.output_lines.append("")
            self.live_line_index = len(self.output_lines) - 1

        chunks = text.split("\n")
        self.output_lines[self.live_line_index] += chunks[0]
        for chunk in chunks[1:]:
            self.output_lines.append(chunk)
            self.live_line_index = len(self.output_lines) - 1
        self.refresh_output()

    def end_live_block(self) -> None:
        if not self.interactive:
            print()
            return
        self.live_line_index = None
        self.refresh_output()

    async def run(self, submit_handler: Callable[[str], Awaitable[None]]) -> str | None:
        self.submit_handler = submit_handler
        if not self.interactive:
            return None
        return await self.app.run_async()

    def exit(self, result: str = "quit") -> None:
        if self.interactive and self.app and self.app.is_running:
            self.app.exit(result=result)

    def attach_mouse_handlers(self) -> None:
        if not self.output_field or not self.input_field:
            return

        output_default_handler = self.output_field.control.mouse_handler
        input_default_handler = self.input_field.control.mouse_handler

        def output_mouse_handler(mouse_event: Any) -> Any:
            if (
                MouseEventType is not None
                and mouse_event.event_type
                in {MouseEventType.SCROLL_UP, MouseEventType.SCROLL_DOWN}
            ):
                self.focus_output()
            if (
                MouseButton is not None
                and MouseEventType is not None
                and mouse_event.button == MouseButton.RIGHT
                and mouse_event.event_type == MouseEventType.MOUSE_UP
            ):
                if self.copy_output_selection():
                    return None
            return output_default_handler(mouse_event)

        def input_mouse_handler(mouse_event: Any) -> Any:
            if (
                MouseButton is not None
                and MouseEventType is not None
                and mouse_event.button == MouseButton.RIGHT
                and mouse_event.event_type == MouseEventType.MOUSE_UP
            ):
                self.focus_input()
                self.paste_to_input()
                return None
            return input_default_handler(mouse_event)

        self.output_field.control.mouse_handler = output_mouse_handler
        self.input_field.control.mouse_handler = input_mouse_handler

    def focus_input(self) -> None:
        if self.interactive and self.app and self.input_field:
            self.app.layout.focus(self.input_field)

    def focus_output(self) -> None:
        if self.interactive and self.app and self.output_field:
            self.app.layout.focus(self.output_field)

    def toggle_focus(self) -> None:
        if (
            not self.interactive
            or not self.app
            or not self.input_field
            or not self.output_field
        ):
            return
        current = self.app.layout.current_control
        if current == self.input_field.control:
            self.focus_output()
        else:
            self.focus_input()

    def scroll_output(self, lines: int) -> None:
        if not self.output_field:
            return
        buffer = self.output_field.buffer
        target_row = buffer.document.cursor_position_row + lines
        target_row = max(0, min(target_row, buffer.document.line_count - 1))
        buffer.cursor_position = buffer.document.translate_row_col_to_index(target_row, 0)
        if self.app:
            self.app.invalidate()

    def scroll_output_to_edge(self, top: bool) -> None:
        if not self.output_field:
            return
        buffer = self.output_field.buffer
        target_row = 0 if top else max(0, buffer.document.line_count - 1)
        buffer.cursor_position = buffer.document.translate_row_col_to_index(target_row, 0)
        if self.app:
            self.app.invalidate()

    def paste_to_input(self) -> None:
        if not self.input_field:
            return
        text = get_clipboard_text()
        if not text:
            return
        if self.interactive:
            self.focus_input()
        self.input_field.buffer.insert_text(text)
        self.show_notice("Pasted")
        if self.app:
            self.app.invalidate()

    def copy_output_selection(self) -> bool:
        if not self.output_field:
            return False
        buffer = self.output_field.buffer
        if buffer.selection_state is None:
            return False
        clipboard_data = buffer.copy_selection()
        text = getattr(clipboard_data, "text", "")
        if not text:
            return False
        copied = set_clipboard_text(text)
        if copied:
            self.show_notice("Copied")
        if self.app:
            self.app.invalidate()
        return copied

    def bottom_toolbar(self) -> str:
        footer = str(self.footer_provider())
        notice = self.get_notice()
        if notice:
            return f"{footer} | {notice}"
        return footer

    def refresh_output(self) -> None:
        if not self.interactive or not self.output_field:
            return
        text = "\n".join(self.output_lines)
        buffer = self.output_field.buffer
        current_document = buffer.document
        current_row = current_document.cursor_position_row
        current_col = current_document.cursor_position_col
        at_bottom = current_row >= max(0, current_document.line_count - 2)
        follow_end = True
        if self.app and self.app.layout.current_control == self.output_field.control:
            follow_end = at_bottom

        if follow_end:
            document = Document(text=text, cursor_position=len(text))
        else:
            new_document = Document(text=text, cursor_position=0)
            target_row = max(0, min(current_row, new_document.line_count - 1))
            target_col = max(0, current_col)
            cursor_position = new_document.translate_row_col_to_index(
                target_row, target_col
            )
            document = Document(text=text, cursor_position=cursor_position)
        self.output_field.buffer.set_document(document, bypass_readonly=True)
        if self.app:
            self.app.invalidate()

    def show_notice(self, text: str, duration: float = 1.5) -> None:
        self.notice_text = text
        self.notice_expires_at = time.monotonic() + duration
        if self.app:
            self.app.invalidate()

    def get_notice(self) -> str:
        if not self.notice_text:
            return ""
        if time.monotonic() >= self.notice_expires_at:
            self.notice_text = ""
            self.notice_expires_at = 0.0
            return ""
        return self.notice_text
