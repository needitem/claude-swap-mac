"""Adding an account without leaving the app.

There is no way to add a Claude subscription account from an ID and password:
``claude auth login`` offers only ``--claudeai`` / ``--console`` / ``--sso``,
and Anthropic exposes no password endpoint. The browser page *is* the real
login. What this module removes is everything around it — no terminal, no
copied commands.

Run without a TTY, ``claude auth login`` is pleasantly scriptable: it opens the
browser, prints the authorize URL, and then blocks reading the pasted code from
stdin. So the app drives the whole sequence and only asks the user for the code.

    logout -> claude auth login -> [user signs in] -> code -> cswap add

The one genuinely dangerous moment is the logout: from there until ``cswap add``
succeeds, the machine has no active login. Every failure path therefore ends in
``cswap switch <previous> --force``, which puts the old account back from its
backup.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time

from cswap_dashboard import cswap

# The authorize URL claude prints on the second line of its login output.
_URL = re.compile(r"https://\S*/oauth/authorize\?\S+")

# A setup-token or a console API key, as accepted by `cswap add-token`.
TOKEN_PREFIXES = ("sk-ant-oat", "sk-ant-api")


class AddAccountError(RuntimeError):
    pass


def _claude() -> str:
    found = shutil.which("claude")
    if found:
        return found
    for candidate in ("~/.local/bin/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        from pathlib import Path

        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path)
    raise AddAccountError("claude CLI not found — is Claude Code installed?")


def auth_status() -> dict:
    """``claude auth status`` as a dict (it prints JSON)."""
    proc = subprocess.run(
        [_claude(), "auth", "status"], capture_output=True, text=True, timeout=30, check=False
    )
    try:
        parsed = json.loads(proc.stdout)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def logout() -> None:
    subprocess.run(
        [_claude(), "auth", "logout"], capture_output=True, text=True, timeout=60, check=False
    )


class LoginSession:
    """A running ``claude auth login``, waiting for its code."""

    def __init__(self, proc: subprocess.Popen, reader: threading.Thread, buf: bytearray):
        self._proc, self._reader, self._buf = proc, reader, buf

    @property
    def output(self) -> str:
        return self._buf.decode("utf-8", "replace")

    def wait_for_url(self, timeout: float = 30.0) -> str:
        """The authorize URL, once claude has printed it."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            match = _URL.search(self.output)
            if match:
                return match.group(0)
            if self._proc.poll() is not None:
                break
            time.sleep(0.2)
        raise AddAccountError(
            "claude auth login did not print a sign-in URL.\n\n" + self.output.strip()[-500:]
        )

    def submit(self, code: str, timeout: float = 180.0) -> None:
        """Hand the pasted code to claude and wait for it to finish."""
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.write((code.strip() + "\n").encode())
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise AddAccountError(f"could not send the code to claude: {exc}") from exc
        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            self.cancel()
            raise AddAccountError("claude auth login timed out") from exc
        if self._proc.returncode != 0:
            raise AddAccountError(
                "sign-in failed.\n\n" + (self.output.strip()[-500:] or "no output")
            )

    def cancel(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait(timeout=10)


def start_login() -> LoginSession:
    """Start ``claude auth login`` with pipes, reading its output in the background."""
    proc = subprocess.Popen(
        [_claude(), "auth", "login"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    buf = bytearray()

    def pump():
        # Byte at a time: the "Paste code here if prompted > " prompt has no
        # trailing newline, so line-buffered reads would block past the URL.
        while True:
            chunk = proc.stdout.read(1) if proc.stdout else b""
            if not chunk:
                return
            buf.extend(chunk)  # not `buf +=`: that would rebind a closed-over name

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    return LoginSession(proc, reader, buf)


def active_account_number() -> int | None:
    """Which cswap account is active right now, if any — the restore target."""
    try:
        status = cswap.list_accounts()
    except cswap.CswapError:
        return None
    for acc in status.get("accounts") or []:
        if isinstance(acc, dict) and acc.get("active") and isinstance(acc.get("number"), int):
            return acc["number"]
    return None


def protect_current_login() -> None:
    """Make sure the login we are about to discard is stored first.

    Without this, adding a second account from a machine whose first account was
    never ``cswap add``-ed would simply destroy that first login.
    """
    status = auth_status()
    if not status.get("loggedIn"):
        return
    try:
        listing = cswap.list_accounts()
    except cswap.CswapError:
        return
    known = {
        str(a.get("email", "")).lower()
        for a in listing.get("accounts") or []
        if isinstance(a, dict)
    }
    if str(status.get("email", "")).lower() not in known:
        cswap.add_current()


def looks_like_token(text: str) -> bool:
    return text.strip().startswith(TOKEN_PREFIXES)
