"""Adding an account without leaving the app.

There is no way to add a Claude subscription account from an ID and password:
``claude auth login`` offers only ``--claudeai`` / ``--console`` / ``--sso``,
and Anthropic exposes no password endpoint. The browser page *is* the real
login. What this module removes is everything around it — no terminal, no
copied commands.

Run without a TTY, ``claude auth login`` is pleasantly scriptable: it opens the
browser, prints the authorize URL, and then blocks reading the pasted code from
stdin. So the app drives the whole sequence and at most asks for the code.

    back up current login -> claude auth login -> [user signs in] -> cswap add

**Do not log out first.** An earlier version ran ``claude auth logout`` before
the login, on the theory that cswap held a backup and could restore it if
anything went wrong. It cannot: logout revokes the refresh token *server-side*,
so restoring the backed-up credentials hands Claude Code a dead token and cswap
reports "re-login needed". Logging in while already logged in works fine and
replaces the credentials only on success, which means a cancelled or failed
login now leaves the existing account untouched — there is nothing to restore.

The login does not always need a code: when the browser session completes the
callback itself, the process exits successfully on its own. :meth:`finish`
therefore accepts an empty code and treats an already-exited process as done.

One consequence of the browser doing the signing in: it uses whatever account
is signed in at claude.com, and will happily re-approve that one without
asking. Adding a *different* account means signing out there first, or opening
the printed URL in a private window.
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

    # How long to keep waiting when there is no code to send, on the chance the
    # browser callback is about to complete the login by itself.
    GRACE_S = 25.0

    def finish(self, code: str = "", timeout: float = 180.0) -> None:
        """Complete the login.

        ``code`` is optional: when the browser session completes the callback
        itself, claude exits successfully without ever reading stdin, and there
        is no code for the user to paste.
        """
        code = (code or "").strip()
        if self._proc.poll() is None:
            if code:
                try:
                    assert self._proc.stdin is not None
                    self._proc.stdin.write((code + "\n").encode())
                    self._proc.stdin.flush()
                except (BrokenPipeError, OSError) as exc:
                    raise AddAccountError(f"could not send the code to claude: {exc}") from exc
                wait_for = timeout
            else:
                wait_for = self.GRACE_S
            try:
                self._proc.wait(timeout=wait_for)
            except subprocess.TimeoutExpired as exc:
                self.cancel()
                if not code:
                    raise AddAccountError(
                        "로그인이 아직 끝나지 않았습니다.\n\n"
                        "브라우저에서 로그인을 마친 뒤, 화면에 코드가 표시되면 "
                        "그 코드를 붙여넣고 다시 시도하세요."
                    ) from exc
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
