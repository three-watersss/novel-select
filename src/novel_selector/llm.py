from __future__ import annotations

import json
import random
from dataclasses import dataclass

from openai import OpenAI

from .config import Settings
from .models import Recommendation, SearchSeed
from .text import truncate_chars


FALLBACK_SEEDS = [
    "完本 玄幻",
    "完本 都市",
    "完本 科幻",
    "完本 历史",
    "完本 仙侠",
    "完本 悬疑",
    "完本 群像",
    "完本 智斗",
    "完本 无敌流",
    "完本 种田",
    "完本 克苏鲁",
    "完本 经营",
    "完本 末世",
    "完本 赛博朋克",
]

EXPLORATION_SEEDS = [
    "冷门完本",
    "高分完本",
    "小众完结",
    "老书完本",
    "黑马完本",
    "精品完结",
]


@dataclass
class LLMClient:
    settings: Settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.openai_api_key)

    def _client(self) -> OpenAI:
        kwargs = {"api_key": self.settings.openai_api_key}
        if self.settings.openai_base_url:
            kwargs["base_url"] = self.settings.openai_base_url
        return OpenAI(**kwargs)

    def generate_search_seeds(self, profile: str, count: int = 12) -> list[SearchSeed]:
        seeds: list[SearchSeed] = []
        if self.enabled:
            prompt = (
                "你是一个网络小说找书助手。根据用户偏好画像，生成适合搜索完本小说的中文关键词。\n"
                "要求：覆盖不同题材，不要只围绕同义词；每个关键词 2-8 个汉字或词组；返回 JSON 数组字符串。\n\n"
                f"偏好画像：\n{profile}\n"
            )
            try:
                response = self._client().chat.completions.create(
                    model=self.settings.openai_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.9,
                )
                content = response.choices[0].message.content or "[]"
                parsed = json.loads(_extract_json(content))
                for value in parsed:
                    if isinstance(value, str) and value.strip():
                        seeds.append(SearchSeed(value=value.strip(), strategy="llm_preference"))
            except Exception:
                seeds = []
        builtins = random.sample(FALLBACK_SEEDS, k=min(len(FALLBACK_SEEDS), count))
        reverse = random.sample(EXPLORATION_SEEDS, k=min(len(EXPLORATION_SEEDS), 4))
        seeds.extend(SearchSeed(value=s, strategy="builtin_genre") for s in builtins)
        seeds.extend(SearchSeed(value=s, strategy="random_exploration") for s in reverse)
        deduped: dict[str, SearchSeed] = {}
        for seed in seeds:
            deduped.setdefault(seed.value, seed)
        return list(deduped.values())[:count]

    def score_candidates(self, profile: str, rows: list, k: int) -> list[Recommendation]:
        if not rows:
            return []
        if self.enabled:
            payload = [
                {
                    "novel_id": row["id"],
                    "title": row["title"],
                    "author": row["author"],
                    "kind": row["kind"],
                    "intro": row["intro"],
                    "chapter_count": row["chapter_count"],
                    "sample": truncate_chars(row["sample_text"], 12000),
                }
                for row in rows
            ]
            prompt = (
                "你正在模拟用户挑选网络小说。请根据偏好画像阅读候选小说前几章试读，选择最值得推荐的作品。\n"
                "返回 JSON 数组，每项字段必须包含：novel_id, score(0-100), reason, risks, style, pacing, verdict。\n"
                f"最多返回 {k} 项，按推荐程度降序。\n\n"
                f"偏好画像：\n{profile}\n\n候选：\n{json.dumps(payload, ensure_ascii=False)}"
            )
            try:
                response = self._client().chat.completions.create(
                    model=self.settings.openai_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                )
                parsed = json.loads(_extract_json(response.choices[0].message.content or "[]"))
                return [_rec(item) for item in parsed[:k] if isinstance(item, dict)]
            except Exception:
                pass
        return heuristic_recommendations(rows, k)


def heuristic_recommendations(rows: list, k: int) -> list[Recommendation]:
    recs: list[Recommendation] = []
    for row in rows[:k]:
        sample = row["sample_text"] or ""
        score = min(95.0, 50.0 + len(sample) / 1000.0 + (10 if row["intro"] else 0))
        recs.append(
            Recommendation(
                novel_id=int(row["id"]),
                score=round(score, 2),
                reason="未配置 LLM，使用启发式兜底：该候选已有完本状态和试读缓存。",
                risks="需要配置 LLM 后才能进行语义级试读判断。",
                style="待 LLM 判断",
                pacing="待 LLM 判断",
                verdict="可作为候选，但建议启用 LLM 后再做最终选择。",
            )
        )
    return recs


def _rec(item: dict) -> Recommendation:
    return Recommendation(
        novel_id=int(item["novel_id"]),
        score=float(item.get("score", 0)),
        reason=str(item.get("reason", "")),
        risks=str(item.get("risks", "")),
        style=str(item.get("style", "")),
        pacing=str(item.get("pacing", "")),
        verdict=str(item.get("verdict", "")),
    )


def _extract_json(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if "\n" in content:
            content = content.split("\n", 1)[1]
    start = min([i for i in [content.find("["), content.find("{")] if i >= 0], default=0)
    end = max(content.rfind("]"), content.rfind("}"))
    return content[start : end + 1] if end >= start else content

