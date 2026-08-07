import json
import sqlite3

from cswap_dashboard import vscode


def make_real_profile(tmp_path):
    """A stand-in for ~/Library/Application Support/Code/User."""
    user = tmp_path / "real" / "User"
    (user / "globalStorage").mkdir(parents=True)
    (user / "snippets").mkdir()
    user.joinpath("settings.json").write_text(
        json.dumps({"editor.fontSize": 13, "extensions.autoUpdate": True})
    )
    user.joinpath("keybindings.json").write_text("[]")
    user.joinpath("snippets", "python.json").write_text("{}")

    db = user / "globalStorage" / "state.vscdb"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
    con.execute(
        "INSERT INTO ItemTable VALUES (?, ?)",
        ('secret://{"extensionId":"vscode.github-authentication","key":"github.auth"}', b"tok"),
    )
    con.commit()
    con.close()
    return user


def test_seed_carries_settings_keybindings_and_snippets(tmp_path):
    real = make_real_profile(tmp_path)
    target = tmp_path / "profile"
    vscode.seed_profile(target, source=real)

    assert (target / "User" / "keybindings.json").read_text() == "[]"
    assert (target / "User" / "snippets" / "python.json").exists()
    assert json.loads((target / "User" / "settings.json").read_text())["editor.fontSize"] == 13


def test_seed_carries_the_github_sign_in(tmp_path):
    """The whole point: a fresh profile must not look like a wiped install."""
    real = make_real_profile(tmp_path)
    target = tmp_path / "profile"
    vscode.seed_profile(target, source=real)

    con = sqlite3.connect(target / "User" / "globalStorage" / "state.vscdb")
    keys = [row[0] for row in con.execute("SELECT key FROM ItemTable")]
    con.close()
    assert any("github-authentication" in k for k in keys)


def test_seed_disables_extension_auto_update(tmp_path):
    """Extensions are shared, and a background updater is the one writer the
    user does not trigger — the case that can corrupt the shared directory."""
    real = make_real_profile(tmp_path)
    target = tmp_path / "profile"
    vscode.seed_profile(target, source=real)
    assert json.loads((target / "User" / "settings.json").read_text())[
        "extensions.autoUpdate"
    ] is False


def test_seed_never_overwrites_an_existing_profile(tmp_path):
    real = make_real_profile(tmp_path)
    target = tmp_path / "profile"
    (target / "User").mkdir(parents=True)
    (target / "User" / "keybindings.json").write_text('["mine"]')

    vscode.seed_profile(target, source=real)
    assert (target / "User" / "keybindings.json").read_text() == '["mine"]'


def test_seed_survives_a_missing_source(tmp_path):
    target = tmp_path / "profile"
    vscode.seed_profile(target, source=tmp_path / "nope")  # must not raise
    assert target.exists()


def test_is_seeded_tracks_the_marker_file(tmp_path):
    target = tmp_path / "profile"
    assert not vscode.is_seeded(target)
    vscode.seed_profile(target, source=make_real_profile(tmp_path))
    assert vscode.is_seeded(target)


def test_user_data_root_is_short_enough_for_a_unix_socket():
    """VS Code puts a socket in the data dir; the path limit is ~103 chars and
    a scratch path under /private/tmp really did fail with listen EINVAL."""
    longest = str(vscode.USER_DATA_ROOT / "99" / "1.13-main.sock")
    assert len(longest) < 90
