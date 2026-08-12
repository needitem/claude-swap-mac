import json
import subprocess
import time

from cswap_dashboard import autoswitch, cswap


def fake_engine(lines, exit_code=0, hold=0.0):
    """A stand-in for `cswap auto --json`: prints events, then exits."""
    body = "".join(f"import sys,time\n" for _ in range(0))  # keep it a plain script
    script = (
        "import sys,time\n"
        f"for line in {lines!r}:\n"
        "    sys.stdout.write(line + '\\n'); sys.stdout.flush()\n"
        f"time.sleep({hold})\n"
        f"sys.exit({exit_code})\n"
    )
    return script + body


def run_with(monkeypatch, tmp_path, script):
    path = tmp_path / "fake-cswap"
    path.write_text(script)
    monkeypatch.setattr(
        cswap, "find_cswap", lambda: "/usr/bin/env"
    )
    # `cswap auto --json` becomes `env python3 <script> auto --json`
    real_popen = subprocess.Popen

    def popen(argv, **kw):
        return real_popen(["python3", str(path), *argv[1:]], **kw)

    monkeypatch.setattr(subprocess, "Popen", popen)


POLL = json.dumps({"schemaVersion": 1, "event": "poll", "threshold": 90.0})
SWITCH = json.dumps(
    {
        "schemaVersion": 1,
        "event": "switch",
        "from": {"number": 1, "email": "a@example.com"},
        "to": {"number": 2, "email": "b@example.com"},
        "reason": "threshold",
    }
)


def test_events_reach_the_callback(monkeypatch, tmp_path):
    seen = []
    run_with(monkeypatch, tmp_path, fake_engine([POLL, SWITCH]))
    engine = autoswitch.AutoSwitch(seen.append)
    engine.start()
    for _ in range(50):
        if len(seen) >= 2:
            break
        time.sleep(0.1)
    engine.stop()
    assert [e["event"] for e in seen[:2]] == ["poll", "switch"]


def test_garbage_lines_are_skipped(monkeypatch, tmp_path):
    seen = []
    run_with(monkeypatch, tmp_path, fake_engine(["not json", POLL]))
    engine = autoswitch.AutoSwitch(seen.append)
    engine.start()
    for _ in range(50):
        if seen:
            break
        time.sleep(0.1)
    engine.stop()
    assert [e["event"] for e in seen] == ["poll"]


def test_an_unexpected_exit_is_reported(monkeypatch, tmp_path):
    seen = []
    run_with(monkeypatch, tmp_path, fake_engine([], exit_code=3))
    engine = autoswitch.AutoSwitch(seen.append)
    engine.start()
    for _ in range(50):
        if seen:
            break
        time.sleep(0.1)
    assert seen and seen[0]["event"] == "error" and "3" in seen[0]["detail"]


def test_stopping_is_not_reported_as_an_error(monkeypatch, tmp_path):
    seen = []
    run_with(monkeypatch, tmp_path, fake_engine([POLL], hold=30))
    engine = autoswitch.AutoSwitch(seen.append)
    engine.start()
    for _ in range(50):
        if seen:
            break
        time.sleep(0.1)
    engine.stop()
    time.sleep(0.5)
    assert [e["event"] for e in seen] == ["poll"]  # no trailing error
    assert not engine.running


def test_start_is_idempotent(monkeypatch, tmp_path):
    run_with(monkeypatch, tmp_path, fake_engine([POLL], hold=30))
    engine = autoswitch.AutoSwitch(lambda e: None)
    engine.start()
    first = engine._proc
    engine.start()
    assert engine._proc is first
    engine.stop()


def test_describe_only_speaks_up_for_events_worth_interrupting_for():
    assert autoswitch.describe(json.loads(POLL)) is None
    assert autoswitch.describe({"event": "no-switch", "reason": "below-threshold"}) is None

    said = autoswitch.describe(json.loads(SWITCH))
    assert "a@example.com" in said and "b@example.com" in said and "threshold" in said

    assert "재로그인" in autoswitch.describe(
        {"event": "account-quarantined", "account": {"email": "x@example.com"}}
    )
    assert "한도" in autoswitch.describe({"event": "all-exhausted"})
    assert "boom" in autoswitch.describe({"event": "error", "detail": "boom"})


def test_switch_events_trigger_a_refresh_list():
    """The dashboard repaints on these, and only these."""
    assert "switch" in autoswitch.SWITCH_EVENTS
    assert "poll" not in autoswitch.SWITCH_EVENTS
    assert "no-switch" not in autoswitch.SWITCH_EVENTS


def test_threshold_parses_a_plain_cli_answer(monkeypatch):
    monkeypatch.setattr(cswap, "run_plain", lambda *a, **k: "autoswitch.threshold  80  (set)")
    assert autoswitch.configured_threshold() == 80.0

    monkeypatch.setattr(cswap, "run_plain", lambda *a, **k: '{"value": 75}')
    assert autoswitch.configured_threshold() == 75.0

    def boom(*a, **k):
        raise cswap.CswapError("nope")

    monkeypatch.setattr(cswap, "run_plain", boom)
    assert autoswitch.configured_threshold() is None
