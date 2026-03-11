from __future__ import annotations

from dataclasses import dataclass
import time


@dataclass
class SessionStatus:
    current: str = "Idle"
    since: float | None = None

    def set(self, status: str) -> None:
        if self.current != status:
            self.current = status
            self.since = time.monotonic()
        elif self.since is None:
            self.since = time.monotonic()

    def clear(self) -> None:
        self.current = "Idle"
        self.since = None

    def format(self) -> str:
        if self.current == "Idle":
            return "Idle"

        spinner_frames = "-\\|/"
        elapsed = 0.0
        if self.since is not None:
            elapsed = time.monotonic() - self.since
        spinner = spinner_frames[int(elapsed * 5) % len(spinner_frames)]
        return f"{spinner} {self.current} {elapsed:.1f}s"
