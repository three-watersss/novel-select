from novel_selector.cli import feedback
from novel_selector.db import Database
from novel_selector.models import NovelCandidate, Recommendation


def add_discovered(db, title):
    run_id = db.create_discovery_run(10, [])
    novel_id, _ = db.upsert_discovered(
        NovelCandidate(
            title=title,
            author="作者",
            source_id="s1",
            source_name="源",
            book_url=f"https://example.test/{title}",
            completed=True,
        ),
        run_id,
    )
    db.save_sample(novel_id, "s1", 1, "第一章内容")
    return novel_id


class UpdatingLLM:
    def __init__(self):
        self.context = None

    def update_preference_profile(self, current_profile, feedback_context):
        self.context = feedback_context
        return current_profile + "\n新版画像"


class FailingLLM:
    def update_preference_profile(self, current_profile, feedback_context):
        raise RuntimeError("boom")


def test_feedback_updates_preference_profile(tmp_path, monkeypatch, capsys):
    db = Database(tmp_path / "app.sqlite3")
    db.init()
    db.add_preference_event("initial_profile", "初始画像")
    novel_id = add_discovered(db, "入选书")
    db.save_recommendations(
        1,
        "test-model",
        "初始画像",
        [Recommendation(novel_id, 90, "推荐理由", "", "文风", "节奏", "结论", "exploration")],
    )
    llm = UpdatingLLM()
    answers = iter([str(novel_id), "喜欢这个探索方向"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    code = feedback(db, llm)

    assert code == 0
    assert db.preference_profile().endswith("新版画像")
    assert llm.context[0]["recommendation_type"] == "exploration"
    assert "Preference profile updated" in capsys.readouterr().out


def test_feedback_keeps_feedback_when_profile_update_fails(tmp_path, monkeypatch, capsys):
    db = Database(tmp_path / "app.sqlite3")
    db.init()
    db.add_preference_event("initial_profile", "初始画像")
    novel_id = add_discovered(db, "入选书")
    db.save_recommendations(
        1,
        "test-model",
        "初始画像",
        [Recommendation(novel_id, 90, "推荐理由", "", "文风", "节奏", "结论")],
    )
    answers = iter([str(novel_id), "仍然喜欢"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    code = feedback(db, FailingLLM())

    with db.connect() as conn:
        feedback_count = conn.execute("SELECT COUNT(*) FROM feedback_events").fetchone()[0]
        profile_updates = conn.execute(
            "SELECT COUNT(*) FROM preference_events WHERE event_type = 'profile_update'"
        ).fetchone()[0]
    assert code == 0
    assert feedback_count == 1
    assert profile_updates == 0
    assert "画像更新失败" in capsys.readouterr().out


def test_unrecommended_sample_can_remain_recommendable(tmp_path):
    db = Database(tmp_path / "app.sqlite3")
    db.init()
    selected_id = add_discovered(db, "被推荐")
    skipped_id = add_discovered(db, "候选但未推荐")
    db.save_recommendations(
        1,
        "test-model",
        "画像",
        [Recommendation(selected_id, 90, "推荐理由", "", "文风", "节奏", "结论")],
    )

    recommendable_ids = [row["id"] for row in db.recommendable_samples(10)]

    assert selected_id not in recommendable_ids
    assert skipped_id in recommendable_ids
