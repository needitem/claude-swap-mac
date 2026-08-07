# cswap-dashboard

A macOS menu bar item and window dashboard for
[claude-swap](https://github.com/realiti4/claude-swap): every Claude Code
account's 5-hour and weekly usage, how much is left, and when each window
resets — in one glance.

![Claude Swap dashboard](screenshot.png)

## What it is

`claude-swap` already does the hard part: it manages multiple Claude Code
logins, tracks usage per account inside a rate-limit-aware polling budget, and
switches between them. It ships a CLI, a TUI, and a menu bar item.

This is a different front end for the same data — a real window, sized for
reading several accounts at once, with a bar per usage window rather than a
line of percentages. It shells out to `cswap ... --json` and renders the
result; it never imports `claude_swap`, never touches your credentials, and
never talks to Anthropic's API itself. Upstream can move without breaking it,
and there is no second polling loop competing for the same rate-limit budget.

## Install

Requires macOS 12+ and Python 3.12+.

**1. Install claude-swap and register an account**

```bash
uv tool install claude-swap
```

Log into Claude Code with each account in turn, running `cswap add` after each
one. Check it worked:

```bash
cswap list
```

**2. Install the dashboard**

```bash
uv tool install git+https://github.com/needitem/claude-swap-mac
```

**3. Build the app bundle**

```bash
git clone https://github.com/needitem/claude-swap-mac
cd claude-swap-mac
./build_app.sh
```

That writes `/Applications/Claude Swap.app` (pass a different directory as the
first argument to put it elsewhere). Launch it from Spotlight or Finder — a
**⇄** appears in the menu bar. Click it → **대시보드 열기**.

To run without the bundle, just run `cswap-dashboard` in a terminal.

### Start it at login

```bash
osascript -e 'tell application "System Events" to make login item at end with properties {path:"/Applications/Claude Swap.app", hidden:true}'
```

## What it shows

Per account, one card:

| | |
|---|---|
| **5시간** | rolling 5-hour window — used %, remaining %, time to reset |
| **7일** | weekly window, same |
| per-model | any per-model weekly limit the account has (e.g. Fable) |

Bars are coloured by how much room is left — green under 60%, amber to 85%,
orange past that, red at the limit — so the colour answers "can I keep working
here?" before you read a number.

A **과속** chip on a weekly window means usage is meaningfully ahead of an even
burn rate for the week: on pace to run out before it resets. `claude-swap`
suppresses this for the first day after a reset, when the comparison is
meaningless.

The active account is outlined. Any other account gets a **전환** button; it
runs `cswap switch N` and repaints. Accounts held out of rotation with
`cswap disable` are dimmed and marked **제외**.

## Accounts that share one quota

If two accounts show a **쿼터 공유** chip, they have the same
`organizationUuid` — and rate limits appear to pool per organization rather
than per account. Users report that accounts sharing an organization show
identical utilisation *and* identical reset times, while accounts in different
organizations are fully independent
([anthropics/claude-code#41886][41886]; [#54464][54464] and [#34888][34888] are
the same thing seen from the confusing end). Anthropic never confirmed any of
it — all three issues were auto-closed as stale — so the dashboard warns rather
than asserts.

It is worth warning about precisely because a dashboard is where the illusion
would form: two names, two cards, two sets of bars that happen to move
together. Switching between such accounts buys you nothing, and nothing in the
numbers says so unless you know where to look.

Check it yourself:

```bash
cswap list --json | python3 -c "import json,sys; [print(a['number'], a.get('organizationUuid'), a['email']) for a in json.load(sys.stdin)['accounts']]"
```

Related: the VS Code extension ignores `CLAUDE_CONFIG_DIR` ([#34888][34888]),
so per-workspace account separation does not work there even though the
terminal CLI honours it. For genuinely parallel accounts use `cswap run`, which
sets `CLAUDE_CONFIG_DIR` for one terminal.

[41886]: https://github.com/anthropics/claude-code/issues/41886
[54464]: https://github.com/anthropics/claude-code/issues/54464
[34888]: https://github.com/anthropics/claude-code/issues/34888

## Opening VS Code as a given account

Every card has a **VS Code** button. It opens a VS Code window bound to that
account, with its own `--user-data-dir` (VS Code allows one instance per data
dir) and a shared `--extensions-dir`, so extensions are not installed twice.

The extension is widely believed to ignore `CLAUDE_CONFIG_DIR`
([#34888][34888]). It does not. The extension **bundles** Claude Code —
`extension.js` contains

```js
function Jo(){ if(process.env.CLAUDE_CONFIG_DIR) return process.env.CLAUDE_CONFIG_DIR
               return path.join(os.homedir(), ".claude") }
```

— so it honours the variable like every other surface. What that issue actually
hit is that VS Code's `claudeCode.environmentVariables` setting only reaches the
*integrated terminal*, never the extension host. Put the variable in the
environment of the VS Code **process** and the extension follows it: measured
here, every child of a VS Code launched that way, extension host included,
carries it.

**A new per-account window is seeded from your real profile.** A bare
`--user-data-dir` opens a VS Code with no settings and, most alarmingly, no
GitHub sign-in — indistinguishable from a wiped installation. So on first
creation the app copies `settings.json`, `keybindings.json`, `snippets/` and
`globalStorage/state.vscdb` across, and asks before doing it. The database is
where sign-ins live, encrypted with the `Code Safe Storage` Keychain key; that
key belongs to the *application*, not to a data dir, so a copy decrypts
normally and GitHub stays signed in. The profiles diverge after that first
copy. Your existing VS Code is never modified.

Extensions are shared rather than reinstalled, which is the one real hazard
here: two VS Code instances writing that directory at once can corrupt
`extensions.json`. Background auto-update is the only writer you do not trigger
yourself, so the seeded settings set `"extensions.autoUpdate": false`. Install
and update extensions from your normal window.

Two more details that bite:

- The data dir holds a unix socket, so its path has the usual ~103-character
  limit. `~/.cswap-vscode/<n>`, deliberately short — a path under `/private/tmp`
  failed with `listen EINVAL`.
- Only **non-active** accounts get a session profile. cswap refuses to make a
  second credential copy of the account that is already the default login
  ("two copies of one account can drift if the server rotates the refresh
  token"), so the active account's window binds to `~/.claude` — which is what
  plain VS Code uses anyway. Profiles are materialised by
  `cswap run <n> -- --version`, so the sharing rules for `settings.json`,
  `CLAUDE.md` and `skills/` stay upstream's.

### The credential namespace is separable from the config dir

Also in the bundle, deriving the Keychain service name:

```js
let t = process.env.CLAUDE_SECURESTORAGE_CONFIG_DIR,
    r = t !== undefined ? !t : !process.env.CLAUDE_CONFIG_DIR,
    n = t !== undefined ? t.normalize("NFC") : configDir(),
    i = r ? "" : `-${sha256(n).slice(0, 8)}`;   // "Claude Code-credentials[-hash8]"
```

So the login and the config directory can be split apart:

| Want | Set |
|---|---|
| Separate account *and* separate history | `CLAUDE_CONFIG_DIR=<profile>` |
| Separate history, **same login** | `CLAUDE_CONFIG_DIR=<profile>` + `CLAUDE_SECURESTORAGE_CONFIG_DIR=""` |
| Same config dir, separate login | `CLAUDE_SECURESTORAGE_CONFIG_DIR=<path>` |

The dashboard does not use the second and third rows yet; they are recorded
because they are not documented anywhere else.

## Adding an account

Menu bar **⇄** → **계정 추가…**. No terminal, no copied commands.

There is no way to add a Claude subscription account from an ID and password —
`claude auth login` offers only `--claudeai` / `--console` / `--sso`, and
Anthropic exposes no password endpoint. The browser page *is* the login. What
the app removes is everything around it:

1. Backs up the account you are logged into now, if it is not stored already
2. Starts `claude auth login`, which opens the browser
3. Takes the code the login page shows — or nothing at all, when the browser
   session completes the callback by itself
4. Runs `cswap add`, and the new card appears

**It does not log you out first**, and that is deliberate. An earlier version
did, reasoning that cswap held a backup to restore from if anything failed. It
does not work: `claude auth logout` revokes the refresh token *server-side*, so
restoring the backup hands Claude Code a dead token and cswap reports
`re-login needed`. Logging in while already logged in replaces the credentials
only on success, so a cancelled or failed login now leaves your existing
account untouched.

One thing the app cannot do for you: the browser signs in with whatever account
is already logged in at claude.com, and re-approves it without asking. **To add
a different account, sign out at claude.com first** — or copy the URL from the
dialog into a private window.

**계정 추가… → 토큰 붙여넣기** skips the browser entirely: paste a setup-token
(`claude setup-token`, `sk-ant-oat…`) or a Console API key (`sk-ant-api…`) and
it goes straight to `cswap add-token`. Note that an API-key account has no
subscription quota, so it shows no usage bars.

If an account ever shows `re-login needed`, its stored refresh token is dead.
Log in as that account and run `cswap add` — it updates the slot in place
rather than adding a duplicate.

## Refreshing

Numbers refresh every 60 seconds, and whenever you open the window or switch.
A failed poll leaves the last good reading on screen rather than blanking it —
`claude-swap` serves usage from a cache with an age, so a brief network blip
should not erase a dashboard that was right a minute ago.

## How the .app works

The bundle is a shell launcher, not a frozen Python app — it finds
`cswap-dashboard` on disk and starts it. One detail is load-bearing:

> The launcher must **not** `exec` the command, and must not leave it as its
> child. It runs `"$APP_BIN" & disown`.

The binary that actually runs the GUI is the uv/pipx venv's `python`, which
lives outside the bundle. If that process inherits the bundle's LaunchServices
registration, macOS 26 accepts its `NSStatusItem` over XPC and then **never
draws it** — the menu bar item silently does not appear, and nothing shows up
in any log. Unsetting `__CFBundleIdentifier` does not help; LaunchServices
makes the association at exec time, not through the environment. Orphaning the
process makes it register with the WindowServer on its own, exactly as running
the command in a terminal does.

Measured on macOS 26.5.1: exec'd from the bundle → no icon; orphaned → icon.

Startup output goes to `~/.claude-swap-backup/menubar-app.log`. After editing
anything inside the bundle, re-sign it:

```bash
codesign --force --sign - "/Applications/Claude Swap.app"
```

## Development

```bash
git clone https://github.com/needitem/claude-swap-mac
cd claude-swap-mac
uv tool install --editable .   # `git pull` now updates the installed command
cswap-dashboard
```

Run against a fixture instead of your real accounts — an active account with a
per-model window, one nearly out of 5-hour quota and ahead of its weekly pace,
and one held out of rotation:

```bash
CSWAP_DASHBOARD_DEMO=1 cswap-dashboard
```

That is also how the screenshot above was taken, so it carries no real email.

`render.py` is pure — it turns a `cswap list --json` payload into HTML with no
AppKit and no subprocess involved, so you can iterate on the layout in a
browser:

```bash
python3 -c "import sys; sys.path.insert(0,'src'); from cswap_dashboard import cswap, render; open('/tmp/preview.html','w').write(render.render(cswap.DEMO))" && open /tmp/preview.html
```

```bash
uv run --with pytest pytest -q
```

## Relationship to claude-swap

Separate project, not a fork. All account handling, credential storage, usage
polling and switching belongs to
[realiti4/claude-swap](https://github.com/realiti4/claude-swap) (MIT) — this
repo contains only the macOS front end and the bundle build.

## License

MIT
