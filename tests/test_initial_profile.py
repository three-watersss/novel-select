from pathlib import Path

from novel_selector.cli import main
from novel_selector.db import Database
from novel_selector.initial_profile import (
    InitialProfileService,
    LocalNovel,
    chunk_size_for_context,
    local_novel_hash,
    split_novel_into_chunks,
)


def test_cli_blocks_without_enough_local_novels(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NOVEL_SELECTOR_DB", str(tmp_path / "app.sqlite3"))
    monkeypatch.setenv("NOVEL_SELECTOR_NOVELS_DIR", str(tmp_path / "novels"))

    code = main(["init"])

    assert code == 1
    assert "5" in capsys.readouterr().out


def test_clear_bypasses_local_novel_check(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "app.sqlite3"
    db_path.write_text("not a real db", encoding="utf-8")
    monkeypatch.setenv("NOVEL_SELECTOR_DB", str(db_path))
    monkeypatch.setenv("NOVEL_SELECTOR_NOVELS_DIR", str(tmp_path / "missing-novels"))

    code = main(["clear", "--yes"])

    assert code == 0
    assert not db_path.exists()
    assert capsys.readouterr().out


def test_init_skips_when_preference_profile_exists(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "app.sqlite3"
    novels_dir = tmp_path / "novels"
    novels_dir.mkdir()
    for index in range(10):
        (novels_dir / f"book-{index}.txt").write_text("chapter 1\nfavorite content", encoding="utf-8")
    db = Database(db_path)
    db.init()
    db.add_preference_event("initial_profile", "existing profile")
    monkeypatch.setenv("NOVEL_SELECTOR_DB", str(db_path))
    monkeypatch.setenv("NOVEL_SELECTOR_NOVELS_DIR", str(novels_dir))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    code = main(["init"])

    assert code == 0
    assert capsys.readouterr().out


def test_chunking_prefers_chapter_boundaries():
    text = "\n\n".join(
        [
            "chapter 1\n" + "a" * 8000,
            "chapter 2\n" + "b" * 8000,
            "chapter 3\n" + "c" * 8000,
        ]
    )
    chunks = split_novel_into_chunks(LocalNovel(Path("x.txt"), "Test Book", text), context_window=8000)

    assert len(chunks) == 3
    assert "chapter 1" in chunks[0].text
    assert "chapter 2" not in chunks[0].text
    assert "chapter 2" in chunks[1].text


def test_context_window_changes_chunk_size():
    assert chunk_size_for_context(1_000_000) > chunk_size_for_context(100_000)


def test_init_caches_and_reuses_local_novel_summaries(tmp_path):
    db = Database(tmp_path / "app.sqlite3")
    db.init()
    novels_dir = tmp_path / "novels"
    novels_dir.mkdir()
    for index in range(10):
        (novels_dir / f"book-{index}.txt").write_text(f"chapter 1\nfavorite content {index}", encoding="utf-8")

    llm = FakeInitialProfileLLM()
    profile = InitialProfileService(db, llm, novels_dir, context_window=8000).initialize()

    assert profile == "profile:10"
    assert llm.summary_calls == 10
    first_text = "chapter 1\nfavorite content 0"
    assert db.cached_local_novel_summary(local_novel_hash(first_text), 8000) == "summary:book-0"

    with db.connect() as conn:
        conn.execute("DELETE FROM preference_events")

    second_llm = FakeInitialProfileLLM()
    profile = InitialProfileService(db, second_llm, novels_dir, context_window=8000).initialize()

    assert profile == "profile:10"
    assert second_llm.summary_calls == 0


class FakeInitialProfileLLM:
    enabled = True

    def __init__(self):
        self.summary_calls = 0

    def summarize_local_novel(self, title, chunks):
        self.summary_calls += 1
        return f"summary:{title}"

    def build_initial_profile(self, novel_summaries):
        return f"profile:{len(novel_summaries)}"
