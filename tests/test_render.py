from cswap_dashboard.render import content_height, render, render_body

ACCOUNT = {
    "number": 1,
    "email": "you@example.com",
    "active": True,
    "usageStatus": "ok",
    "usage": {
        "fiveHour": {"pct": 42.0, "countdown": "1h 08m", "clock": "18:10"},
        "sevenDay": {"pct": 91.0, "countdown": "12h 38m", "clock": "Aug 8 01:00"},
        "scoped": [{"pct": 2.0, "name": "Fable", "countdown": "12h 38m"}],
    },
    "usageAgeSeconds": 130.0,
}


def payload(*accounts):
    return {"schemaVersion": 1, "accounts": list(accounts)}


def test_renders_every_window_with_remaining_and_reset():
    html = render_body(payload(ACCOUNT))
    assert "5시간" in html and "7일" in html and "Fable" in html
    assert "42%" in html and "58% 남음" in html  # used and remaining
    assert "1h 08m 후" in html and "(18:10)" in html
    assert "사용 중" in html


def test_band_colours_track_utilisation():
    html = render_body(payload(ACCOUNT))
    assert 'class="ok"' in html      # 5h at 42%
    assert 'class="danger"' in html  # 7d at 91%


def test_inactive_account_gets_a_switch_button():
    other = {**ACCOUNT, "number": 2, "email": "b@example.com", "active": False}
    html = render_body(payload(ACCOUNT, other))
    assert html.count('class="switch" data-num="2"') == 1
    assert 'data-num="1"' not in html  # the active one has nothing to switch to


def test_disabled_account_is_marked():
    off = {**ACCOUNT, "active": False, "disabled": True}
    assert "제외" in render_body(payload(off))


def test_falls_back_to_last_good_usage():
    stale = {
        "number": 3,
        "email": "c@example.com",
        "usageStatus": "unavailable",
        "usage": None,
        "lastGoodUsage": {"fiveHour": {"pct": 7.0, "countdown": "2h"}},
        "lastGoodAgeSeconds": 600.0,
    }
    html = render_body(payload(stale))
    assert "7%" in html and "최근 값" in html and "10분 전 측정" in html


def test_ahead_of_pace_chip_only_when_flagged():
    assert "과속" not in render_body(payload(ACCOUNT))
    ahead = {
        **ACCOUNT,
        "usage": {
            **ACCOUNT["usage"],
            "sevenDay": {**ACCOUNT["usage"]["sevenDay"], "aheadOfPace": True, "expectedPct": 40.0},
        },
    }
    assert "과속" in render_body(payload(ahead))


def test_empty_and_error_states():
    assert "등록된 계정이 없습니다" in render_body(payload())
    assert "불러올 수 없음" in render_body(None, "cswap not found")


def test_html_is_escaped():
    evil = {**ACCOUNT, "email": "<script>alert(1)</script>@x.com"}
    assert "<script>alert(1)" not in render_body(payload(evil))


def test_full_document_wraps_the_body():
    html = render(payload(ACCOUNT))
    assert html.startswith("<!doctype html>") and "you@example.com" in html


def test_content_height_grows_with_accounts_and_stays_bounded():
    one = content_height(payload(ACCOUNT))
    two = content_height(payload(ACCOUNT, {**ACCOUNT, "number": 2}))
    assert 200 <= one < two <= 900
    assert content_height(payload(*[{**ACCOUNT, "number": n} for n in range(20)])) == 900
    assert content_height(None, "boom") == 240
