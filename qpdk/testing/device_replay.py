"""Device-session replay for plugin gauntlets.

Loads qsys-device-recording/1 fixtures (see device-recordings/README.md in the
plugin repos) and answers a plugin's commands with what the REAL
device said during capture. The plugin under test runs in QSYSRuntime and
writes to a MockSsh / MockTcpSocket; the test loop calls ReplayDevice.pump()
after ticking the virtual clock, and the replay injects recorded responses via
simulate_data().

Matching model: command text (stripped) -> FIFO queue of recorded responses.
The same command recorded N times yields its N responses in order; after the
queue empties the LAST response repeats (steady-state polling reads the same
device state unless the recording says otherwise). Unmatched commands raise in
strict mode — a plugin talking to reality the fixture never saw is a coverage
gap to record on the live device, not to invent here. Per-test synthetic
responses belong in `overrides`, never in the fixture files.
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any


class UnrecordedCommandError(AssertionError):
    """Plugin sent a command the recording bank has no response for."""


class DeviceRecording:
    """One parsed fixture file."""

    def __init__(self, data: dict[str, Any], path: Path | None = None):
        if data.get("format") != "qsys-device-recording/1":
            raise ValueError(f"not a qsys-device-recording/1 fixture: {path}")
        self.device: dict[str, Any] = data.get("device", {})
        self.captured: str | None = data.get("captured")
        self.path = path
        self.exchanges: list[dict[str, Any]] = data.get("exchanges", [])

    @classmethod
    def load(cls, path: str | Path) -> DeviceRecording:
        p = Path(path)
        return cls(json.loads(p.read_text(encoding="utf-8")), p)

    def commands(self) -> list[str]:
        return [e["tx"] for e in self.exchanges if e["type"] == "cmd"]


class ReplayDevice:
    """Answer plugin TX from one or more recordings.

    Args:
        recordings: fixture file paths and/or DeviceRecording objects.
            Later recordings' responses queue AFTER earlier ones for the
            same command.
        overrides: {command_text: response_text} checked BEFORE recordings —
            the place for test-specific synthetic responses.
        strict: raise UnrecordedCommandError on unmatched commands (default).
            When False, unmatched commands are recorded in self.unmatched and
            silently unanswered (the plugin sees device silence — itself a
            legitimate gauntlet scenario).
        eol: line ending appended to injected responses when the recorded
            text does not end with a newline.
    """

    def __init__(self, recordings: list[Any], overrides: dict[str, str] | None = None,
                 strict: bool = True, eol: str = "\r\n"):
        self._bank: dict[str, deque[str]] = {}
        self._last: dict[str, str] = {}
        self.overrides = dict(overrides or {})
        self.strict = strict
        self.eol = eol
        self.unmatched: list[str] = []
        self.answered: list[str] = []
        self._txbuf = ""
        self._writes_seen = 0
        for rec in recordings:
            if not isinstance(rec, DeviceRecording):
                rec = DeviceRecording.load(rec)
            for e in rec.exchanges:
                if e["type"] in ("cmd", "multiline"):
                    q = self._bank.setdefault(e["tx"].strip(), deque())
                    q.append(e["rx"])

    # -- lookup --------------------------------------------------------

    def response_for(self, cmd: str) -> str | None:
        cmd = cmd.strip()
        if cmd in self.overrides:
            return self.overrides[cmd]
        q = self._bank.get(cmd)
        if q:
            rx = q.popleft()
            self._last[cmd] = rx
            return rx
        if cmd in self._last:
            return self._last[cmd]
        return None

    # -- pumping a mock socket ------------------------------------------

    def pump(self, sock) -> int:
        """Answer any new complete command lines the plugin has written.

        Works with MockSsh / MockTcpSocket (anything with get_written_data()
        and simulate_data()). Returns the number of commands answered this
        call. Call after each runtime tick.
        """
        writes = sock.get_written_data()
        new = writes[self._writes_seen:]
        self._writes_seen = len(writes)
        if not new:
            return 0
        self._txbuf += "".join(new)
        n = 0
        while "\n" in self._txbuf:
            line, self._txbuf = self._txbuf.split("\n", 1)
            cmd = line.strip()
            if not cmd:
                continue
            rx = self.response_for(cmd)
            if rx is None:
                self.unmatched.append(cmd)
                if self.strict:
                    raise UnrecordedCommandError(
                        f"plugin sent {cmd!r} but the recording bank has no "
                        f"response for it (recorded commands: "
                        f"{len(self._bank)}; add an override or record a "
                        f"session covering it)")
                continue
            self.answered.append(cmd)
            if rx and not rx.endswith("\n"):
                rx += self.eol
            if rx:
                sock.simulate_data(rx)
            n += 1
        return n

    # -- async events ----------------------------------------------------

    def inject_feedback(self, sock, recording: DeviceRecording,
                        index: int = 0) -> str:
        """Replay the index-th feedback window of a recording into the socket."""
        fbs = [e for e in recording.exchanges if e["type"] == "feedback"]
        rx = fbs[index]["rx"]
        if rx:
            sock.simulate_data(rx if rx.endswith("\n") else rx + self.eol)
        return rx


def load_bank(root: str | Path, pattern: str = "*.json") -> list[DeviceRecording]:
    """Load every fixture under a directory (recursive)."""
    root = Path(root)
    out = []
    for p in sorted(root.rglob(pattern)):
        try:
            out.append(DeviceRecording.load(p))
        except ValueError:
            continue
    return out
