from novel_selector.db import Database
from novel_selector.models import NovelCandidate
from novel_selector.text import novel_fingerprint


def test_discovered_novel_excludes_future_discovery(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.init()
    run_id = db.create_discovery_run(10, [])
    candidate = NovelCandidate(
        title="测试小说",
        author="作者",
        source_id="s1",
        source_name="源",
        book_url="https://example.test/book/1",
        completed=True,
        completion_evidence="完结",
    )
    novel_id, created = db.upsert_discovered(candidate, run_id)
    assert novel_id > 0
    assert created is True
    assert db.has_discovered(novel_fingerprint("测试小说", "作者"))

    second = NovelCandidate(
        title="《测试小说》",
        author=" 作者 ",
        source_id="s2",
        source_name="另一个源",
        book_url="https://example.test/book/2",
        completed=True,
        completion_evidence="完本",
    )
    novel_id_2, created_2 = db.upsert_discovered(second, run_id)
    assert novel_id_2 == novel_id
    assert created_2 is False


def test_source_capability_whitelist_filters_sources(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.init()
    sources = [
        {"bookSourceName": "ok", "bookSourceUrl": "https://ok.test", "bookSourceType": 0},
        {"bookSourceName": "bad", "bookSourceUrl": "https://bad.test", "bookSourceType": 0},
    ]
    db.replace_sources(sources)

    with db.connect() as conn:
        rows = conn.execute("SELECT id, name FROM sources ORDER BY name").fetchall()
    bad_id = rows[0]["id"]
    ok_id = rows[1]["id"]
    db.save_source_capability(
        ok_id,
        {
            "supports_search": True,
            "returns_metadata": True,
            "detects_completion": True,
            "supports_toc": True,
            "supports_content": True,
            "passed": True,
            "success_rate": 1.0,
        },
    )
    db.save_source_capability(bad_id, {"last_error_type": "empty_search"})

    assert db.has_source_capabilities() is True
    assert [source["bookSourceName"] for source in db.list_sources(capable_only=True)] == ["ok"]
    summary = db.source_capability_summary()
    assert summary["passed"] == 1
    assert summary["top_errors"]["empty_search"] == 1
