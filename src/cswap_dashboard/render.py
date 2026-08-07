"""Render the ``cswap list --json`` payload as the dashboard's HTML.

Pure functions only — no AppKit, no subprocess — so the layout can be exercised
from a test or dumped to a file without launching the GUI.
"""

from __future__ import annotations

from html import escape

# Utilization bands. The point of the dashboard is to answer "can I keep
# working on this account?" at a glance, so the fill colour carries that answer
# and the number is only confirmation.
_OK, _WARN, _DANGER = 60.0, 85.0, 100.0


def _band(pct: float) -> str:
    if pct >= _DANGER:
        return "spent"
    if pct >= _WARN:
        return "danger"
    if pct >= _OK:
        return "warn"
    return "ok"


def _window_row(label: str, window: dict | None, *, stale: bool = False) -> str:
    """One usage bar: label, fill, used/remaining, and when it resets."""
    if not isinstance(window, dict) or not isinstance(window.get("pct"), (int, float)):
        return (
            f'<div class="row empty"><span class="lbl">{escape(label)}</span>'
            f'<span class="na">사용량 없음</span></div>'
        )

    pct = max(0.0, min(100.0, float(window["pct"])))
    remaining = 100.0 - pct
    countdown = window.get("countdown")
    clock = window.get("clock")

    reset_bits = []
    if countdown:
        reset_bits.append(f"{escape(str(countdown))} 후")
    if clock:
        reset_bits.append(f"({escape(str(clock))})")
    reset = " ".join(reset_bits) or "—"

    # "ahead of pace" only ever appears on weekly windows, and only once the
    # week is old enough for the comparison to mean anything (cswap suppresses
    # it for the first day). It is the one signal here that is about trend
    # rather than level, so it gets its own chip instead of colouring the bar.
    pace = ""
    if window.get("aheadOfPace"):
        expected = window.get("expectedPct")
        tip = f"주 진행률 대비 초과 (예상 {expected:.0f}%)" if isinstance(expected, (int, float)) else "주 진행률 대비 초과"
        pace = f'<span class="chip pace" title="{escape(tip)}">과속</span>'

    return f"""<div class="row{' stale' if stale else ''}">
  <span class="lbl">{escape(label)}</span>
  <span class="bar"><i class="{_band(pct)}" style="width:{pct:.4g}%"></i></span>
  <span class="pct">{pct:.0f}%</span>
  <span class="left">{remaining:.0f}% 남음</span>
  <span class="reset">{reset}{pace}</span>
</div>"""


def shared_quota_groups(accounts: list) -> dict[str, list]:
    """Map each ``organizationUuid`` held by more than one account to its members.

    Rate limits appear to pool per organization rather than per account: users
    report that accounts sharing an ``organizationUuid`` show identical
    utilisation *and* identical reset times, while accounts in different
    organizations are fully independent (anthropics/claude-code#41886, with
    #54464 and #34888 as the confused-looking symptoms). Anthropic never
    confirmed it — every one of those issues was auto-closed as stale — so this
    is a warning, not a verdict.

    It matters here because a dashboard is exactly where the illusion would
    form: two cards, two names, two sets of bars that happen to move together.
    Switching between such accounts buys nothing, and the numbers give no hint
    unless you are told where to look.
    """
    groups: dict[str, list] = {}
    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        uuid = acc.get("organizationUuid")
        if isinstance(uuid, str) and uuid:
            groups.setdefault(uuid, []).append(acc)
    return {uuid: members for uuid, members in groups.items() if len(members) > 1}


def _shared_notice(groups: dict[str, list]) -> str:
    if not groups:
        return ""
    lines = []
    for members in groups.values():
        names = ", ".join(
            escape(str(m.get("alias") or m.get("email") or m.get("number"))) for m in members
        )
        lines.append(f"<li>{names}</li>")
    return (
        '<section class="notice"><b>같은 조직의 계정은 한도를 공유합니다</b>'
        "<ul>" + "".join(lines) + "</ul>"
        "<span>아래 계정들은 <code>organizationUuid</code>가 같습니다. 사용량이 함께 "
        "움직이고 리셋 시각도 같다면 전환해도 여유가 생기지 않습니다. "
        "(anthropics/claude-code#41886 — Anthropic 공식 확인은 없습니다)</span></section>"
    )


def _account_card(acc: dict, shared_with: list | None = None) -> str:
    number = acc.get("number")
    email = str(acc.get("email") or "?")
    alias = acc.get("alias")
    active = bool(acc.get("active"))
    disabled = bool(acc.get("disabled"))
    status = str(acc.get("usageStatus") or "")

    # `usage` is the decision-grade measurement; when it is absent cswap may
    # still hand us `lastGoodUsage`, which is worth drawing (dimmed) rather than
    # showing an empty card — "42% as of 6 minutes ago" beats "unknown".
    usage = acc.get("usage")
    stale = False
    if not isinstance(usage, dict):
        usage = acc.get("lastGoodUsage") if isinstance(acc.get("lastGoodUsage"), dict) else None
        stale = usage is not None

    chips = []
    if active:
        chips.append('<span class="chip active">사용 중</span>')
    if disabled:
        chips.append('<span class="chip off" title="자동 전환 대상에서 제외됨">제외</span>')
    if status and status != "ok":
        chips.append(f'<span class="chip bad">{escape(status)}</span>')
    if stale:
        chips.append('<span class="chip stale-chip">최근 값</span>')
    if shared_with:
        peers = ", ".join(str(p.get("email") or p.get("number")) for p in shared_with)
        chips.append(
            f'<span class="chip shared" title="{escape(peers)} 와(과) 같은 조직 — '
            '한도를 함께 씁니다">쿼터 공유</span>'
        )

    rows = [
        _window_row("5시간", (usage or {}).get("fiveHour"), stale=stale),
        _window_row("7일", (usage or {}).get("sevenDay"), stale=stale),
    ]
    for scoped in (usage or {}).get("scoped") or []:
        if isinstance(scoped, dict):
            rows.append(_window_row(str(scoped.get("name") or "모델"), scoped, stale=stale))

    age = acc.get("usageAgeSeconds")
    if stale:
        age = acc.get("lastGoodAgeSeconds")
    age_note = ""
    if isinstance(age, (int, float)):
        mins = int(age // 60)
        age_note = f'<span class="age">{mins}분 전 측정</span>' if mins else '<span class="age">방금 측정</span>'

    action = ""
    if not active and isinstance(number, int):
        action = f'<button class="switch" data-num="{number}">전환</button>'

    title = escape(alias) if alias else escape(email)
    sub = f'<span class="sub">{escape(email)}</span>' if alias else ""

    return f"""<section class="card{' is-active' if active else ''}{' is-off' if disabled else ''}">
  <header>
    <span class="num">{escape(str(number))}</span>
    <span class="who">{title}{sub}</span>
    <span class="chips">{''.join(chips)}</span>
    {action}
  </header>
  {''.join(rows)}
  <footer>{age_note}</footer>
</section>"""


def render_body(payload: dict | None, error: str | None = None) -> str:
    """Just the cards — what a refresh swaps into ``document.body``.

    Refreshes replace the body rather than reloading the document so the
    window does not flicker and the scroll position survives. The click
    handler is bound to ``document``, not to the buttons, so it outlives the
    swap even though the inline ``<script>`` is not re-executed.
    """
    if error:
        body = f'<div class="fatal"><h2>불러올 수 없음</h2><pre>{escape(error)}</pre></div>'
    else:
        accounts = (payload or {}).get("accounts") or []
        if not accounts:
            body = (
                '<div class="fatal"><h2>등록된 계정이 없습니다</h2>'
                "<pre>Claude Code에 로그인한 뒤 터미널에서:\n\n    cswap add</pre></div>"
            )
        else:
            groups = shared_quota_groups(accounts)
            cards = []
            for acc in accounts:
                if not isinstance(acc, dict):
                    continue
                members = groups.get(acc.get("organizationUuid") or "", [])
                peers = [m for m in members if m.get("number") != acc.get("number")]
                cards.append(_account_card(acc, peers))
            body = _shared_notice(groups) + "".join(cards)
    return body


def render(payload: dict | None, error: str | None = None) -> str:
    """Full HTML document for the dashboard's first load."""
    return _DOC.replace("{{BODY}}", render_body(payload, error))


# Card chrome (header + padding + footer + margin) and one usage row, in points,
# measured against the rendered stylesheet above. Keep in step with the CSS.
_CHROME, _CARD, _ROW = 28, 88, 18


def content_height(payload: dict | None, error: str | None = None) -> int:
    """Window height that fits the content, so one account is not a tall
    window of empty space and six accounts do not need scrolling."""
    accounts = (payload or {}).get("accounts") or []
    if error or not accounts:
        return 240
    total = _CHROME
    groups = shared_quota_groups(accounts)
    if groups:
        total += 78 + 16 * sum(len(m) for m in groups.values())
    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        usage = acc.get("usage")
        if not isinstance(usage, dict):
            usage = acc.get("lastGoodUsage") if isinstance(acc.get("lastGoodUsage"), dict) else {}
        rows = 2 + len((usage or {}).get("scoped") or [])
        total += _CARD + rows * _ROW
    return max(200, min(900, total))


_DOC = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{
  --bg:#f6f6f7; --card:#fff; --ink:#1b1b1f; --muted:#71717a; --line:#e5e5e8;
  --ok:#2e9e6b; --warn:#d99a2b; --danger:#d4562f; --spent:#b3261e;
  --accent:#c4593a; --track:#ececf0;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#17171a; --card:#202024; --ink:#eceaea; --muted:#9b9ba3; --line:#2e2e34;
    --ok:#4cbc88; --warn:#e0ae4e; --danger:#e97a55; --spent:#f2685c;
    --accent:#e8926f; --track:#2b2b31;
  }
}
*{box-sizing:border-box}
body{
  margin:0; padding:14px; background:var(--bg); color:var(--ink);
  font:13px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text",sans-serif;
  -webkit-font-smoothing:antialiased;
}
.card{
  background:var(--card); border:1px solid var(--line); border-radius:11px;
  padding:11px 13px 8px; margin-bottom:10px;
}
.card.is-active{border-color:var(--accent); box-shadow:0 0 0 1px var(--accent) inset}
.card.is-off{opacity:.62}
header{display:flex; align-items:center; gap:8px; margin-bottom:9px}
.num{
  min-width:19px; height:19px; border-radius:5px; background:var(--track);
  color:var(--muted); font-size:11px; font-weight:600;
  display:inline-flex; align-items:center; justify-content:center; flex:none;
}
.is-active .num{background:var(--accent); color:#fff}
.who{font-weight:600; font-size:13.5px; display:flex; align-items:baseline; gap:6px; min-width:0}
.who .sub{font-weight:400; font-size:11px; color:var(--muted); overflow:hidden; text-overflow:ellipsis}
.chips{display:flex; gap:4px; flex-wrap:wrap}
.chip{
  font-size:10px; font-weight:600; padding:1.5px 6px; border-radius:99px;
  background:var(--track); color:var(--muted); white-space:nowrap;
}
.chip.active{background:var(--accent); color:#fff}
.chip.bad{background:var(--spent); color:#fff}
.chip.pace{background:transparent; color:var(--danger); border:1px solid currentColor; margin-left:6px}
.chip.shared{background:var(--warn); color:#fff}
.notice{
  background:var(--card); border:1px solid var(--warn); border-left-width:3px;
  border-radius:9px; padding:9px 12px; margin-bottom:10px; font-size:11.5px;
}
.notice b{font-size:12.5px}
.notice ul{margin:5px 0; padding-left:17px}
.notice span{color:var(--muted); display:block}
.notice code{font-size:11px}
button.switch{
  margin-left:auto; flex:none; font:inherit; font-size:11.5px; font-weight:600;
  padding:3px 11px; border-radius:7px; cursor:pointer;
  border:1px solid var(--line); background:var(--card); color:var(--ink);
}
button.switch:hover{border-color:var(--accent); color:var(--accent)}
button.switch:disabled{opacity:.5; cursor:default}
.row{
  display:grid; grid-template-columns:44px 1fr 34px 62px auto;
  align-items:center; gap:8px; padding:2.5px 0;
}
.row.stale{opacity:.66}
.row.empty{grid-template-columns:44px 1fr}
.lbl{color:var(--muted); font-size:11.5px}
.bar{background:var(--track); height:7px; border-radius:99px; overflow:hidden}
.bar i{display:block; height:100%; border-radius:99px; transition:width .3s ease}
.bar i.ok{background:var(--ok)} .bar i.warn{background:var(--warn)}
.bar i.danger{background:var(--danger)} .bar i.spent{background:var(--spent)}
.pct{font-variant-numeric:tabular-nums; font-weight:600; text-align:right; font-size:12px}
.left{font-variant-numeric:tabular-nums; color:var(--muted); font-size:11px; text-align:right}
.reset{color:var(--muted); font-size:11px; white-space:nowrap}
.na{color:var(--muted); font-size:11px}
footer{margin-top:5px; min-height:13px}
.age{color:var(--muted); font-size:10.5px}
.fatal{padding:22px 8px; text-align:center; color:var(--muted)}
.fatal h2{color:var(--ink); font-size:14px; margin:0 0 10px}
.fatal pre{
  white-space:pre-wrap; text-align:left; font-size:11.5px; line-height:1.6;
  background:var(--card); border:1px solid var(--line); border-radius:9px; padding:11px;
}
</style></head><body>
{{BODY}}
<script>
document.addEventListener('click', function (e) {
  var b = e.target.closest('button.switch');
  if (!b) return;
  b.disabled = true; b.textContent = '전환 중…';
  window.webkit.messageHandlers.cswap.postMessage({action:'switch', number:+b.dataset.num});
});
</script>
</body></html>"""
