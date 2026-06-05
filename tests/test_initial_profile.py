from pathlib import Path

from novel_selector.cli import main
from novel_selector.db import Database
from novel_selector.initial_profile import (
    LocalNovel,
    chunk_size_for_context,
    split_novel_into_chunks,
)


def test_cli_blocks_without_enough_local_novels(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NOVEL_SELECTOR_DB", str(tmp_path / "app.sqlite3"))
    monkeypatch.setenv("NOVEL_SELECTOR_NOVELS_DIR", str(tmp_path / "novels"))

    code = main(["init"])

    assert code == 1
    assert "至少 20 本" in capsys.readouterr().out


def test_clear_bypasses_local_novel_check(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "app.sqlite3"
    db_path.write_text("not a real db", encoding="utf-8")
    monkeypatch.setenv("NOVEL_SELECTOR_DB", str(db_path))
    monkeypatch.setenv("NOVEL_SELECTOR_NOVELS_DIR", str(tmp_path / "missing-novels"))

    code = main(["clear", "--yes"])

    assert code == 0
    assert not db_path.exists()
    assert "数据库已清理" in capsys.readouterr().out


def test_init_skips_when_preference_profile_exists(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "app.sqlite3"
    novels_dir = tmp_path / "novels"
    novels_dir.mkdir()
    for index in range(20):
        (novels_dir / f"book-{index}.txt").write_text("第1章\n喜欢的小说内容", encoding="utf-8")
    db = Database(db_path)
    db.init()
    db.add_preference_event("initial_profile", "已有画像")
    monkeypatch.setenv("NOVEL_SELECTOR_DB", str(db_path))
    monkeypatch.setenv("NOVEL_SELECTOR_NOVELS_DIR", str(novels_dir))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    code = main(["init"])

    assert code == 0
    assert "已经初始化过" in capsys.readouterr().out


def test_chunking_prefers_chapter_boundaries():
    text = "\n\n".join(
        [
            "第1章\n" + "一" * 8000,
            "第2章\n" + "二" * 8000,
            "第3章\n" + "三" * 8000,
        ]
    )
    chunks = split_novel_into_chunks(LocalNovel(Path("x.txt"), "测试书", text), context_window=8000)

    assert len(chunks) == 3
    assert "第1章" in chunks[0].text
    assert "第2章" not in chunks[0].text
    assert "第2章" in chunks[1].text


def test_context_window_changes_chunk_size():
    assert chunk_size_for_context(1_000_000) > chunk_size_for_context(100_000)
