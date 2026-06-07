from types import SimpleNamespace
from pathlib import Path

from novel_selector.config import Settings
from novel_selector.llm import LLMClient


def settings(api_key="key"):
    return Settings(
        db_path=Path("test.sqlite3"),
        source_url="",
        openai_api_key=api_key,
        openai_base_url=None,
        openai_model="test-model",
        request_timeout=1,
        user_agent="test",
        log_dir=Path("logs"),
        log_level="INFO",
        log_max_file_bytes=1,
        log_backups=1,
        log_max_dir_bytes=1,
        novels_dir=Path("novels"),
        context_window=8000,
    )


class FakeClient:
    def __init__(self, content):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: self._create(**kwargs)))
        self.content = content
        self.prompts = []

    def _create(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


def test_generate_search_seeds_reserves_exploration_budget(monkeypatch):
    fake = FakeClient('["智斗完本","群像仙侠","幕后流","克系玄幻","慢热修真","无女主"]')
    monkeypatch.setattr(LLMClient, "_client", lambda self: fake)

    seeds = LLMClient(settings()).generate_search_seeds("喜欢智斗", count=12)

    strategies = [seed.strategy for seed in seeds]
    assert len(seeds) == 12
    assert strategies.count("llm_preference") == 6
    assert strategies.count("builtin_genre") == 4
    assert strategies.count("random_exploration") == 2


def test_generate_search_seeds_fills_when_llm_returns_too_few(monkeypatch):
    fake = FakeClient('["智斗完本"]')
    monkeypatch.setattr(LLMClient, "_client", lambda self: fake)

    seeds = LLMClient(settings()).generate_search_seeds("喜欢智斗", count=12)

    assert len(seeds) == 12
    assert seeds[0].strategy == "llm_preference"
    assert sum(seed.strategy == "builtin_genre" for seed in seeds) >= 4
    assert sum(seed.strategy == "random_exploration" for seed in seeds) >= 2


def test_score_candidates_parses_recommendation_type(monkeypatch):
    fake = FakeClient(
        '[{"novel_id":1,"recommendation_type":"exploration","score":88,'
        '"reason":"可能拓展口味","risks":"","style":"清爽","pacing":"中快","verdict":"值得试"}]'
    )
    monkeypatch.setattr(LLMClient, "_client", lambda self: fake)
    rows = [
        {
            "id": 1,
            "title": "测试书",
            "author": "作者",
            "kind": "玄幻",
            "intro": "简介",
            "chapter_count": 3,
            "sample_text": "正文",
        }
    ]

    recs = LLMClient(settings()).score_candidates("喜欢智斗", rows, k=2)

    assert recs[0].recommendation_type == "exploration"
    assert "最多包含 1 项不强匹配画像" in fake.prompts[0]


def test_score_candidates_prompt_allows_empty_recommendations(monkeypatch):
    fake = FakeClient("[]")
    monkeypatch.setattr(LLMClient, "_client", lambda self: fake)
    rows = [
        {
            "id": 1,
            "title": "测试书",
            "author": "作者",
            "kind": "玄幻",
            "intro": "简介",
            "chapter_count": 3,
            "sample_text": "正文",
        }
    ]

    recs = LLMClient(settings()).score_candidates("喜欢智斗", rows, k=5)

    assert recs == []
    assert "请返回空数组 []，不要为了凑数硬推荐" in fake.prompts[0]
    assert "可以少于 5 项" in fake.prompts[0]


def test_score_candidates_defaults_missing_recommendation_type(monkeypatch):
    fake = FakeClient(
        '[{"novel_id":1,"score":80,"reason":"匹配","risks":"","style":"清爽","pacing":"中快","verdict":"可看"}]'
    )
    monkeypatch.setattr(LLMClient, "_client", lambda self: fake)
    rows = [
        {
            "id": 1,
            "title": "测试书",
            "author": "作者",
            "kind": "玄幻",
            "intro": "简介",
            "chapter_count": 3,
            "sample_text": "正文",
        }
    ]

    recs = LLMClient(settings()).score_candidates("喜欢智斗", rows, k=1)

    assert recs[0].recommendation_type == "preference"
