"""Thin wrapper around the ``cswap`` CLI.

Everything this app knows about accounts comes from ``cswap ... --json``. We
never import ``claude_swap`` or touch credentials, the usage store, or the
Keychain ourselves: the CLI owns that, including the rate-limit-aware polling
cadence, so shelling out is both simpler and better behaved than duplicating it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

# Where a uv/pipx/Homebrew install of claude-swap normally lands. Checked in
# order; PATH is consulted last because a GUI app launched from Finder inherits
# a minimal PATH that usually has none of these on it.
_CANDIDATES = (
    "~/.local/bin/cswap",
    "/opt/homebrew/bin/cswap",
    "/usr/local/bin/cswap",
)


class CswapError(RuntimeError):
    """The cswap CLI is missing, failed, or returned something unusable."""


def find_cswap() -> str:
    """Absolute path to the cswap executable, or raise CswapError."""
    for candidate in _CANDIDATES:
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    found = shutil.which("cswap")
    if found:
        return found
    raise CswapError(
        "cswap was not found.\n\n"
        "Install it with:\n"
        "    uv tool install 'claude-swap[menubar]'"
    )


def _run(args: list[str], timeout: float) -> dict:
    """Run cswap with --json and return the parsed object."""
    exe = find_cswap()
    try:
        proc = subprocess.run(
            [exe, *args, "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CswapError(f"cswap {' '.join(args)} timed out after {timeout:.0f}s") from exc

    # cswap puts the JSON payload on stdout and human notices on stderr, and it
    # emits a JSON error object with a non-zero exit code on a handled failure —
    # so parse stdout first and only fall back to the exit code.
    payload: dict | None = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = None

    if payload is not None and "error" in payload:
        err = payload["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise CswapError(message or "cswap reported an error")
    if payload is None:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise CswapError(detail or f"cswap {' '.join(args)} exited {proc.returncode}")
    return payload


# A fixture with the states worth looking at while working on the layout: an
# active account with a per-model window, one nearly out of 5-hour quota and
# ahead of its weekly pace, and one held out of rotation. Set
# CSWAP_DASHBOARD_DEMO=1 to render this instead of real accounts — useful for
# iterating on the design, and for taking a screenshot without publishing your
# own email address.
DEMO = {
    "schemaVersion": 1,
    "activeAccountNumber": 1,
    "accounts": [
        {
            "number": 1, "email": "personal@example.com", "active": True, "usageStatus": "ok",
            "organizationUuid": "11111111-1111-1111-1111-111111111111",
            "usage": {
                "fiveHour": {"pct": 43.0, "countdown": "1h 08m", "clock": "18:10"},
                "sevenDay": {"pct": 29.0, "countdown": "11h 27m", "clock": "Aug 8 01:00"},
                "scoped": [{"name": "Fable", "pct": 6.0, "countdown": "11h 27m", "clock": "Aug 8 00:59"}],
            },
            "usageAgeSeconds": 95.0,
        },
        {
            "number": 2, "alias": "work", "email": "work@example.com",
            "active": False, "usageStatus": "ok",
            "organizationUuid": "22222222-2222-2222-2222-222222222222",
            "usage": {
                "fiveHour": {"pct": 88.0, "countdown": "42m", "clock": "14:20"},
                "sevenDay": {"pct": 74.0, "countdown": "3d 04h", "clock": "Aug 11 09:00",
                             "aheadOfPace": True, "expectedPct": 52.0},
            },
            "usageAgeSeconds": 260.0,
        },
        {
            # Same org as #2 on purpose: this is what pooled quota looks like.
            "number": 3, "email": "backup@example.com", "active": False,
            "disabled": True, "usageStatus": "ok",
            "organizationUuid": "22222222-2222-2222-2222-222222222222",
            "usage": {
                "fiveHour": {"pct": 0.0, "countdown": "5h 00m", "clock": "18:45"},
                "sevenDay": {"pct": 12.0, "countdown": "6d 02h", "clock": "Aug 13 22:00"},
            },
            "usageAgeSeconds": 540.0,
        },
    ],
}


def run_plain(args: list[str], timeout: float = 120.0) -> str:
    """Run a cswap subcommand that has no --json form; return its output."""
    exe = find_cswap()
    try:
        proc = subprocess.run(
            [exe, *args], capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise CswapError(f"cswap {' '.join(args)} timed out") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise CswapError(detail or f"cswap {' '.join(args)} exited {proc.returncode}")
    return (proc.stdout or "").strip()


def add_current(alias: str | None = None) -> str:
    """``cswap add`` — register whichever account Claude Code is logged into."""
    return run_plain(["add", *(["--alias", alias] if alias else [])])


def add_token(token: str, alias: str | None = None) -> str:
    """``cswap add-token`` — register a setup-token or API key directly."""
    return run_plain(["add-token", token, *(["--alias", alias] if alias else [])])


def restore(number: int) -> str:
    """``cswap switch N --force`` — re-activate stored credentials.

    ``--force`` skips backing up the current login first, which is exactly what
    is wanted when recovering from an add that was cancelled after logout:
    there is no current login worth keeping, and a plain switch to the account
    cswap still believes is active would be a no-op that leaves you logged out.
    """
    return run_plain(["switch", str(number), "--force"])


def list_accounts(timeout: float = 30.0) -> dict:
    """``cswap list --json`` — every account with usage, quota and reset times."""
    if os.environ.get("CSWAP_DASHBOARD_DEMO"):
        return DEMO
    return _run(["list"], timeout)


def switch_to(number: int, timeout: float = 60.0) -> dict:
    """``cswap switch N --json`` — make account ``number`` the active one."""
    return _run(["switch", str(number)], timeout)
