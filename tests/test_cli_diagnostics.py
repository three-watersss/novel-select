import os

from novel_selector.cli import main
from novel_selector.db import Database
from novel_selector.interactive import InteractiveCommand, print_interactive_help


def test_status_bypasses_local_novel_check(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NOVEL_SELECTOR_DB", str(tmp_path / "app.sqlite3"))
    monkeypatch.setenv("NOVEL_SELECTOR_NOVELS_DIR", str(tmp_path / "missing-novels"))

    code = main(["status"])

    output = capsys.readouterr().out
    assert code == 0
    assert "Novel Selector Status" in output
    assert "not initialized" in output


def test_show_profile_missing_profile_prompts_init(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NOVEL_SELECTOR_DB", str(tmp_path / "app.sqlite3"))
    monkeypatch.setenv("NOVEL_SELECTOR_NOVELS_DIR", str(tmp_path / "missing-novels"))

    code = main(["show-profile"])

    assert code == 1
    assert "novel-selector init" in capsys.readouterr().out


def test_show_profile_outputs_existing_profile(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "app.sqlite3"
    db = Database(db_path)
    db.init()
    db.add_preference_event("initial_profile", "喜欢群像和智斗")
    monkeypatch.setenv("NOVEL_SELECTOR_DB", str(db_path))
    monkeypatch.setenv("NOVEL_SELECTOR_NOVELS_DIR", str(tmp_path / "missing-novels"))

    code = main(["show-profile"])

    assert code == 0
    assert "喜欢群像和智斗" in capsys.readouterr().out


def test_doctor_reports_failures_without_leaking_api_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NOVEL_SELECTOR_DB", str(tmp_path / "app.sqlite3"))
    monkeypatch.setenv("NOVEL_SELECTOR_NOVELS_DIR", str(tmp_path / "missing-novels"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-do-not-print")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4-flash")

    code = main(["doctor"])

    output = capsys.readouterr().out
    assert code == 1
    assert "[FAIL]" in output
    assert "sk-secret-do-not-print" not in output


def test_status_aggregates_source_health(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "app.sqlite3"
    db = Database(db_path)
    db.init()
    db.replace_sources(
        [
            {"bookSourceName": "ok", "bookSourceUrl": "https://ok.test", "bookSourceType": 0},
            {"bookSourceName": "bad", "bookSourceUrl": "https://bad.test", "bookSourceType": 0},
        ]
    )
    # Use the concrete IDs from the sources table to satisfy foreign keys.
    with db.connect() as conn:
        rows = conn.execute("SELECT id FROM sources ORDER BY name").fetchall()
    db.update_source_health(rows[0]["id"], "error", "boom")
    db.update_source_health(rows[1]["id"], "ok")
    monkeypatch.setenv("NOVEL_SELECTOR_DB", str(db_path))
    monkeypatch.setenv("NOVEL_SELECTOR_NOVELS_DIR", str(tmp_path / "missing-novels"))

    code = main(["status"])

    output = capsys.readouterr().out
    assert code == 0
    assert "error=1" in output
    assert "ok=1" in output


def test_bare_command_enters_interactive_shell(monkeypatch):
    seen = {}

    def fake_shell(settings, parser, command_help, execute):
        seen["commands"] = [item.name for item in command_help]
        return 0

    monkeypatch.setattr("novel_selector.cli.run_interactive_shell", fake_shell)

    assert main([]) == 0
    assert "status" in seen["commands"]
    assert "doctor" in seen["commands"]
    assert "exit" in seen["commands"]


def test_global_option_without_command_enters_interactive_shell(monkeypatch):
    called = {}

    def fake_shell(settings, parser, command_help, execute):
        called["ok"] = True
        return 0

    monkeypatch.setattr("novel_selector.cli.run_interactive_shell", fake_shell)

    assert main(["--log-level", "DEBUG"]) == 0
    assert called["ok"] is True


def test_interactive_help_paginates_for_short_terminals(monkeypatch, capsys):
    commands = [InteractiveCommand(f"cmd{i}", f"description {i}") for i in range(8)]
    monkeypatch.setattr("shutil.get_terminal_size", lambda fallback: os.terminal_size((80, 7)))
    answers = iter(["", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    print_interactive_help(commands)

    output = capsys.readouterr().out
    assert "-- more --" in output
    assert "/cmd0" in output
    assert "/cmd7" in output
