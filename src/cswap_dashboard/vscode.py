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


# What gets copied from the real profile into a new per-account one. Plain
# files and directories; state.vscdb is handled separately because it is a live
# SQLite database.
_SEED_ITEMS = ("settings.json", "keybindings.json", "snippets")

REAL_USER_DIR = Path("~/Library/Application Support/Code/User").expanduser()


def is_seeded(user_data: Path) -> bool:
    return (user_data / "User" / "settings.json").exists()


def seed_profile(user_data: Path, source: Path = REAL_USER_DIR) -> None:
    """Copy the parts of your real VS Code profile worth sharing.

    A bare ``--user-data-dir`` opens a VS Code with no settings, no keybindings
    and — most alarmingly — no GitHub sign-in, which looks exactly like a wiped
    installation. Everything here is copied **once**, when the per-account
    directory is first created; the two diverge afterwards.

    ``globalStorage/state.vscdb`` is where the sign-ins live, encrypted with the
    ``Code Safe Storage`` Keychain key. That key belongs to the *application*,
    not to a user-data-dir, so a copied database decrypts normally in the new
    profile and GitHub stays signed in.
    """
    import shutil
    import sqlite3

    target = user_data / "User"
    target.mkdir(parents=True, exist_ok=True)
    if not source.is_dir():
        return

    for name in _SEED_ITEMS:
        src, dst = source / name, target / name
        if dst.exists() or not src.exists():
            continue
        try:
            shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)
        except OSError:
            pass  # a missing convenience is not worth failing the launch over

    # Extensions are shared with the real profile (see launch()), and VS Code
    # writing to that directory from two instances at once is what can corrupt
    # it. Background auto-update is the only writer the user does not trigger
    # deliberately, so turn it off in the copies.
    settings = target / "settings.json"
    try:
        import json

        data = json.loads(settings.read_text()) if settings.exists() else {}
        if not isinstance(data, dict):
            data = {}
        data["extensions.autoUpdate"] = False
        settings.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    except (OSError, ValueError):
        pass

    db_src = source / "globalStorage" / "state.vscdb"
    db_dst = target / "globalStorage" / "state.vscdb"
    if db_src.exists() and not db_dst.exists():
        db_dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            # The backup API, not a file copy: the source may be open in a
            # running VS Code, and copying it byte-wise could catch a
            # half-written page or miss the -wal.
            with sqlite3.connect(f"file:{db_src}?mode=ro", uri=True) as src_db, \
                 sqlite3.connect(db_dst) as dst_db:
                src_db.backup(dst_db)
        except (sqlite3.Error, OSError):
            db_dst.unlink(missing_ok=True)  # better empty than half-copied


def pinned_windows() -> dict[int, int]:
    """Account number -> how many VS Code windows are currently pinned to it.

    "사용 중" on a card means *default login*, not "the account your work is
    actually being billed to". Those differ the moment a pinned window exists:
    it keeps using its own account no matter what the default is, so the
    dashboard can say account 2 is in use while every request you make goes to
    account 1. Counting the windows is the cheapest way to stop that being
    invisible.

    Detected from the command line rather than the environment: the windows this
    app opens carry ``--user-data-dir <root>/<n>``, so no per-process env read
    is needed.
    """
    try:
        listing = subprocess.run(
            ["ps", "-Ao", "command="], capture_output=True, text=True, timeout=10, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    counts: dict[int, int] = {}
    root = str(USER_DATA_ROOT)
    for line in listing.splitlines():
        # Only the main process; VS Code's helpers repeat the flag.
        if "/MacOS/Code" not in line or f"--user-data-dir {root}/" not in line:
            continue
        tail = line.split(f"--user-data-dir {root}/", 1)[1].split()[0]
        if tail.isdigit():
            counts[int(tail)] = counts.get(int(tail), 0) + 1
    return counts


def launch(number: int, active: bool) -> Path:
    """Open a VS Code window bound to this account. Returns the user-data-dir."""
    binary = find_vscode()
    user_data = USER_DATA_ROOT / str(number)
    first_time = not is_seeded(user_data)
    user_data.mkdir(parents=True, exist_ok=True)
    if first_time:
        seed_profile(user_data)

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
