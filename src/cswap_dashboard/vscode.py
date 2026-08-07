"""Open VS Code bound to a specific account.

The VS Code extension is widely believed to ignore ``CLAUDE_CONFIG_DIR``
(anthropics/claude-code#34888). It does not. The extension *bundles* Claude
Code — ``extension.js`` contains

    function Jo(){ if(process.env.CLAUDE_CONFIG_DIR) return process.env.CLAUDE_CONFIG_DIR
                   return path.join(os.homedir(), ".claude") }

— so it honours the variable like every other surface. What the issue actually
hit is that VS Code's ``claudeCode.environmentVariables`` setting only reaches
the *integrated terminal*, never the extension host. Put the variable in the
environment of the VS Code **process** and the extension follows it: measured
on this machine, every child of a VS Code launched that way — extension host
included — carries it.

Two constraints shape the launch:

* VS Code runs one instance per ``--user-data-dir``, so a second account needs
  its own. ``--extensions-dir`` is passed separately so extensions stay shared
  rather than being installed twice.
* That directory holds a unix socket, and the path has the usual ~103-character
  limit — hence a short ``~/.cswap-vscode/<n>`` rather than anything nested.

Only non-active accounts get a profile. cswap deliberately refuses to make a
second credential copy of the account that is already the default login ("two
copies of one account can drift if the server rotates the refresh token"), so
for the active account there is nothing to bind to but ``~/.claude`` — which is
exactly what plain VS Code already uses.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from cswap_dashboard import cswap

# Every user-data-dir lives directly under here: see the socket length limit.
USER_DATA_ROOT = Path("~/.cswap-vscode").expanduser()

_APPS = (
    "/Applications/Visual Studio Code.app/Contents/MacOS/Code",
    "/Applications/Visual Studio Code - Insiders.app/Contents/MacOS/Code - Insiders",
    "~/Applications/Visual Studio Code.app/Contents/MacOS/Code",
)

_EXTENSION_DIRS = ("~/.vscode/extensions", "~/.vscode-insiders/extensions")


class VSCodeError(RuntimeError):
    pass


def find_vscode() -> str:
    for candidate in _APPS:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise VSCodeError(
        "VS Code를 찾을 수 없습니다.\n\n"
        "/Applications/Visual Studio Code.app 에 설치되어 있어야 합니다."
    )


def _shared_extensions_dir() -> str | None:
    for candidate in _EXTENSION_DIRS:
        path = Path(candidate).expanduser()
        if path.is_dir():
            return str(path)
    return None


def session_profile(number: int) -> Path | None:
    """The cswap session profile for this account, if it has been created.

    Laid out by cswap as ``<backup>/sessions/<num>-<email-slug>/``; matched on
    the slot prefix so the email slug does not have to be reproduced here.
    """
    sessions = Path("~/.claude-swap-backup/sessions").expanduser()
    if not sessions.is_dir():
        return None
    for child in sorted(sessions.iterdir()):
        if child.is_dir() and child.name.startswith(f"{number}-"):
            return child
    return None


def ensure_profile(number: int) -> Path:
    """Materialise the session profile, letting cswap do the work.

    ``cswap run N -- --version`` runs claude just long enough to print its
    version and exit, and cswap builds the profile on the way — credentials,
    plus its own sharing rules for settings.json / CLAUDE.md / skills. Doing it
    this way rather than assembling the directory here means the sharing
    behaviour is upstream's, and stays upstream's.
    """
    existing = session_profile(number)
    if existing is not None:
        return existing
    cswap.run_plain(["run", str(number), "--", "--version"], timeout=120.0)
    created = session_profile(number)
    if created is None:
        raise VSCodeError(
            f"계정 {number}의 세션 프로필을 만들지 못했습니다.\n\n"
            f"터미널에서 확인해보세요:  cswap run {number} -- --version"
        )
    return created


def launch(number: int, active: bool) -> Path:
    """Open a VS Code window bound to this account. Returns the user-data-dir."""
    binary = find_vscode()
    user_data = USER_DATA_ROOT / str(number)
    user_data.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    if active:
        # The default login already is this account; binding to ~/.claude is
        # both correct and the only option cswap allows.
        env.pop("CLAUDE_CONFIG_DIR", None)
    else:
        env["CLAUDE_CONFIG_DIR"] = str(ensure_profile(number))

    argv = [binary, "--user-data-dir", str(user_data)]
    extensions = _shared_extensions_dir()
    if extensions:
        argv += ["--extensions-dir", extensions]

    subprocess.Popen(
        argv,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # outlive the dashboard
    )
    return user_data
