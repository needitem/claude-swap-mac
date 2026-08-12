"""Supervise ``cswap auto`` — the auto-switch engine — as a child process.

The engine itself stays upstream's. ``cswap auto --json`` emits one JSON object
per line (``poll``, ``switch``, ``no-switch``, ``account-quarantined``,
``all-exhausted``, ``error``), so supervising the process and reading that
stream gives the menu bar everything it needs without importing
``claude_swap``, re-implementing the threshold policy, or opening a second
polling loop against the usage API.

Policy — threshold, cooldown, hysteresis, strategy — lives in cswap's own
``settings.json`` under ``autoswitch.*``, so this toggle and ``cswap auto`` in a
terminal are the same engine with the same configuration. Change it with
``cswap config set autoswitch.threshold 80``.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading

from cswap_dashboard import cswap

# The contract is explicitly additive — new event kinds and fields may appear —
# so unknown ones are passed through to the callback rather than dropped.
SWITCH_EVENTS = ("switch", "account-quarantined", "all-exhausted", "error")


def configured_threshold() -> float | None:
    """``autoswitch.threshold`` as cswap currently has it, for the menu label."""
    try:
        raw = cswap.run_plain(["config", "get", "autoswitch.threshold"], timeout=20.0)
    except cswap.CswapError:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            for key in ("value", "autoswitch.threshold"):
                if isinstance(parsed.get(key), (int, float)):
                    return float(parsed[key])
        if isinstance(parsed, (int, float)):
            return float(parsed)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"\d+(?:\.\d+)?", raw)
    return float(match.group(0)) if match else None


def external_engine_running() -> bool:
    """Is a ``cswap auto`` already running that we did not start?

    Two engines are not corrupting — they share ``autoswitch_state.json`` under
    a file lock — but they double the usage polling against a budget that is
    deliberately tight, so it is worth telling the user rather than silently
    stacking.
    """
    try:
        found = subprocess.run(
            ["pgrep", "-f", r"^[^ ]*/python[0-9.]* [^ ]*/cswap auto( |$)"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(found.stdout.strip())


class AutoSwitch:
    """Start/stop ``cswap auto`` and forward its events."""

    def __init__(self, on_event):
        self._on_event = on_event
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            self._proc = subprocess.Popen(
                [cswap.find_cswap(), "auto", "--json"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,  # line buffered: events should surface as they happen
            )
            proc = self._proc
        threading.Thread(target=self._pump, args=(proc,), daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _pump(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout or ():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                self._on_event(event)
        code = proc.wait()
        with self._lock:
            expected = self._proc is not proc  # stop() already cleared it
        if not expected and code not in (0, -15):  # -15 == SIGTERM from stop()
            self._on_event({"event": "error", "detail": f"cswap auto exited ({code})"})


def describe(event: dict) -> str | None:
    """A one-line, human summary — or None for events not worth interrupting for."""
    kind = event.get("event")
    if kind == "switch":
        src = (event.get("from") or {}).get("email") or event.get("from")
        dst = (event.get("to") or {}).get("email") or event.get("to")
        reason = event.get("reason")
        line = f"{src} → {dst}" if src and dst else "계정을 전환했습니다"
        return f"{line} ({reason})" if reason else line
    if kind == "account-quarantined":
        who = (event.get("account") or {}).get("email") or event.get("account")
        return f"{who}: 재로그인 필요 (토큰 만료)"
    if kind == "all-exhausted":
        return "모든 계정이 한도에 도달했습니다"
    if kind == "error":
        return f"자동 전환 오류: {event.get('detail') or event.get('message') or '알 수 없음'}"
    return None  # poll / no-switch: the dashboard already shows the numbers
