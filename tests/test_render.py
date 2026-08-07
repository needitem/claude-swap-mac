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


ORG_A = "34d83544-a7e0-0000-0000-000000000000"
ORG_B = "1b8fb384-9be0-0000-0000-000000000000"


def acct(number, org, **kw):
    return {**ACCOUNT, "number": number, "email": f"a{number}@example.com",
            "active": number == 1, "organizationUuid": org, **kw}


def test_accounts_in_one_org_are_flagged_as_sharing_quota():
    html = render_body(payload(acct(1, ORG_A), acct(2, ORG_A), acct(3, ORG_B)))
    assert html.count("쿼터 공유") == 2          # only the two in ORG_A
    assert "같은 조직의 계정은 한도를 공유합니다" in html
    assert "a3@example.com 와(과)" not in html   # the lone account is not implicated


def test_no_notice_when_every_account_is_its_own_org():
    html = render_body(payload(acct(1, ORG_A), acct(2, ORG_B)))
    assert "쿼터 공유" not in html
    assert "같은 조직의 계정은" not in html


def test_accounts_without_an_org_uuid_are_never_grouped():
    html = render_body(payload(acct(1, ""), {**ACCOUNT, "number": 2, "active": False}))
    assert "쿼터 공유" not in html


def test_shared_quota_groups_returns_only_real_groups():
    from cswap_dashboard.render import shared_quota_groups

    groups = shared_quota_groups([acct(1, ORG_A), acct(2, ORG_A), acct(3, ORG_B), "junk"])
    assert list(groups) == [ORG_A]
    assert [a["number"] for a in groups[ORG_A]] == [1, 2]


def test_the_notice_costs_height():
    plain = content_height(payload(acct(1, ORG_A), acct(2, ORG_B)))
    shared = content_height(payload(acct(1, ORG_A), acct(2, ORG_A)))
    assert shared > plain
