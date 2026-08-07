import subprocess
import threading

import pytest

from cswap_dashboard import add_account, cswap

SAMPLE_OUTPUT = (
    "Opening browser to sign in…\n"
    "If the browser didn't open, visit: "
    "https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a&"
    "response_type=code&scope=user%3Aprofile+user%3Ainference&state=IAxiWz\n"
    "Paste code here if prompted > "
)


class FakeProc:
    """Enough of Popen for LoginSession: a byte stream in, a pipe out."""

    def __init__(self, output=b"", returncode=0):
        self._out = output
        self.returncode = returncode
        self.written = bytearray()
        self._done = False
        self.killed = False
        self.stdin = self
        self.stdout = self
        self._pos = 0

    # stdout side
    def read(self, n):
        if self._pos >= len(self._out):
            return b""
        chunk = self._out[self._pos : self._pos + n]
        self._pos += n
        return chunk

    # stdin side
    def write(self, data):
        self.written += data

    def flush(self):
        pass

    def poll(self):
        return self.returncode if self._done else None

    def wait(self, timeout=None):
        self._done = True
        return self.returncode

    def kill(self):
        self.killed = True
        self._done = True


def session_for(proc):
    buf = bytearray()

    def pump():
        while True:
            chunk = proc.stdout.read(1)
            if not chunk:
                return
            buf.extend(chunk)

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    thread.join(timeout=2)
    return add_account.LoginSession(proc, thread, buf)


def test_extracts_the_authorize_url():
    session = session_for(FakeProc(SAMPLE_OUTPUT.encode()))
    url = session.wait_for_url(timeout=2)
    assert url.startswith("https://claude.com/cai/oauth/authorize?")
    assert "state=IAxiWz" in url  # not truncated at the first &


def test_missing_url_raises_with_the_output_for_context():
    proc = FakeProc(b"something went wrong\n", returncode=1)
    proc.wait()  # already exited
    session = session_for(proc)
    with pytest.raises(add_account.AddAccountError, match="did not print a sign-in URL"):
        session.wait_for_url(timeout=1)


def test_submit_sends_the_code_with_a_newline():
    proc = FakeProc(SAMPLE_OUTPUT.encode())
    session = session_for(proc)
    session.submit("  abc123  ")
    assert bytes(proc.written) == b"abc123\n"


def test_submit_raises_when_claude_exits_nonzero():
    proc = FakeProc(SAMPLE_OUTPUT.encode(), returncode=1)
    session = session_for(proc)
    with pytest.raises(add_account.AddAccountError, match="sign-in failed"):
        session.submit("abc123")


def test_cancel_kills_a_running_login():
    proc = FakeProc(SAMPLE_OUTPUT.encode())
    session = session_for(proc)
    session.cancel()
    assert proc.killed


def test_token_shapes():
    assert add_account.looks_like_token("sk-ant-oat01-xxx")
    assert add_account.looks_like_token("  sk-ant-api03-xxx  ")
    assert not add_account.looks_like_token("hunter2")
    assert not add_account.looks_like_token("")


def test_protect_current_login_stores_an_unknown_login(monkeypatch):
    calls = []
    monkeypatch.setattr(add_account, "auth_status", lambda: {"loggedIn": True, "email": "New@Example.com"})
    monkeypatch.setattr(
        cswap, "list_accounts", lambda: {"accounts": [{"email": "old@example.com"}]}
    )
    monkeypatch.setattr(cswap, "add_current", lambda: calls.append("added"))
    add_account.protect_current_login()
    assert calls == ["added"]


def test_protect_current_login_skips_an_already_stored_one(monkeypatch):
    calls = []
    monkeypatch.setattr(add_account, "auth_status", lambda: {"loggedIn": True, "email": "old@example.com"})
    monkeypatch.setattr(
        cswap, "list_accounts", lambda: {"accounts": [{"email": "OLD@example.com"}]}
    )
    monkeypatch.setattr(cswap, "add_current", lambda: calls.append("added"))
    add_account.protect_current_login()
    assert calls == []  # matched case-insensitively


def test_protect_current_login_does_nothing_when_logged_out(monkeypatch):
    calls = []
    monkeypatch.setattr(add_account, "auth_status", lambda: {"loggedIn": False})
    monkeypatch.setattr(cswap, "add_current", lambda: calls.append("added"))
    add_account.protect_current_login()
    assert calls == []


def test_active_account_number(monkeypatch):
    monkeypatch.setattr(
        cswap,
        "list_accounts",
        lambda: {"accounts": [{"number": 1, "active": False}, {"number": 2, "active": True}]},
    )
    assert add_account.active_account_number() == 2

    def boom():
        raise cswap.CswapError("nope")

    monkeypatch.setattr(cswap, "list_accounts", boom)
    assert add_account.active_account_number() is None


def test_restore_uses_force(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cswap, "run_plain", lambda args, **kw: seen.setdefault("args", args) or ""
    )
    cswap.restore(3)
    assert seen["args"] == ["switch", "3", "--force"]


def test_add_token_passes_the_token_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cswap, "run_plain", lambda args, **kw: seen.setdefault("args", args) or ""
    )
    cswap.add_token("sk-ant-oat01-x", alias="work")
    assert seen["args"] == ["add-token", "sk-ant-oat01-x", "--alias", "work"]


def test_auth_status_survives_non_json(monkeypatch):
    monkeypatch.setattr(add_account, "_claude", lambda: "/bin/echo")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="not json", stderr=""),
    )
    assert add_account.auth_status() == {}
