from __future__ import annotations

import json
import random
from dataclasses import dataclass

from openai import OpenAI

from .config import Settings
from .logger import llm_event, prompt_summary
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
        preference_count = min(6, count)
        builtin_count = min(4, max(0, count - preference_count))
        exploration_count = min(2, max(0, count - preference_count - builtin_count))
        seeds: list[SearchSeed] = []
        if self.enabled:
            prompt = (
                "你是一个网络小说找书助手。根据用户偏好画像，生成适合搜索完本小说的中文关键词。\n"
                f"要求：返回 {preference_count} 个画像相关关键词，覆盖不同题材，不要只围绕同义词；"
                "每个关键词 2-8 个汉字或词组；返回 JSON 数组字符串。\n\n"
                f"偏好画像：\n{profile}\n"
            )
            llm_event(
                "request_start",
                model=self.settings.openai_model,
                purpose="generate_search_seeds",
                prompt=prompt_summary(prompt),
            )
            try:
                response = self._client().chat.completions.create(
                    model=self.settings.openai_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.9,
                )
                content = response.choices[0].message.content or "[]"
                llm_event(
                    "response_received",
                    model=self.settings.openai_model,
                    purpose="generate_search_seeds",
                    raw_response=content,
                )
                parsed = json.loads(_extract_json(content))
                for value in parsed:
                    if isinstance(value, str) and value.strip():
                        seeds.append(SearchSeed(value=value.strip(), strategy="llm_preference"))
            except json.JSONDecodeError as exc:
                llm_event(
                    "parse_error",
                    "ERROR",
                    model=self.settings.openai_model,
                    purpose="generate_search_seeds",
                    error=str(exc),
                )
                seeds = []
            except Exception as exc:
                llm_event(
                    "request_error",
                    "ERROR",
                    model=self.settings.openai_model,
                    purpose="generate_search_seeds",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                seeds = []
        else:
            llm_event("fallback_used", purpose="generate_search_seeds", reason="missing_api_key")
        preference = _dedupe(seeds)[:preference_count]
        builtins = [
            SearchSeed(value=s, strategy="builtin_genre")
            for s in random.sample(FALLBACK_SEEDS, k=len(FALLBACK_SEEDS))
        ]
        explorations = [
            SearchSeed(value=s, strategy="random_exploration")
            for s in random.sample(EXPLORATION_SEEDS, k=len(EXPLORATION_SEEDS))
        ]
        result = _dedupe(
            preference
            + _take_new(builtins, preference, builtin_count)
            + _take_new(explorations, preference + builtins, exploration_count)
        )
        if len(result) < count:
            result = _dedupe(result + _take_new(builtins + explorations, result, count - len(result)))
        return result[:count]

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
                "返回 JSON 数组，每项字段必须包含：novel_id, recommendation_type, score(0-100), reason, risks, style, pacing, verdict。\n"
                "recommendation_type 只能是 preference 或 exploration。\n"
                + (
                    f"最多返回 {k} 项：其中 {k - 1} 项应强匹配用户偏好，recommendation_type=preference；"
                    "另 1 项应是不强匹配画像但你判断用户可能会喜欢的探索推荐，recommendation_type=exploration。"
                    if k > 1
                    else "最多返回 1 项，recommendation_type=preference。"
                )
                + "\n按综合推荐价值降序。\n\n"
                f"偏好画像：\n{profile}\n\n候选：\n{json.dumps(payload, ensure_ascii=False)}"
            )
            llm_event(
                "request_start",
                model=self.settings.openai_model,
                purpose="score_candidates",
                candidate_count=len(rows),
                requested_recommendations=k,
                prompt=prompt_summary(prompt),
            )
            try:
                response = self._client().chat.completions.create(
                    model=self.settings.openai_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                )
                content = response.choices[0].message.content or "[]"
                llm_event(
                    "response_received",
                    model=self.settings.openai_model,
                    purpose="score_candidates",
                    raw_response=content,
                )
                parsed = json.loads(_extract_json(content))
                return [_rec(item) for item in parsed[:k] if isinstance(item, dict)]
            except json.JSONDecodeError as exc:
                llm_event(
                    "parse_error",
                    "ERROR",
                    model=self.settings.openai_model,
                    purpose="score_candidates",
                    error=str(exc),
                )
            except Exception as exc:
                llm_event(
                    "request_error",
                    "ERROR",
                    model=self.settings.openai_model,
                    purpose="score_candidates",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
        else:
            llm_event("fallback_used", purpose="score_candidates", reason="missing_api_key", candidate_count=len(rows))
        return heuristic_recommendations(rows, k)

    def update_preference_profile(self, current_profile: str, feedback_context: list[dict]) -> str:
        if not self.enabled:
            raise RuntimeError("LLM is required to update the preference profile")
        prompt = (
            "你正在根据用户对小说推荐的反馈更新用户偏好画像。请输出一份新的完整中文偏好画像，"
            "用于后续搜索和推荐。\n"
            "要求：保留仍然有效的长期偏好；吸收本轮选择/跳过理由；区分强偏好、弱偏好、探索方向、明确避雷；"
            "不要只追加流水账，不要输出 JSON。\n\n"
            f"当前画像：\n{current_profile}\n\n"
            f"本轮反馈：\n{json.dumps(feedback_context, ensure_ascii=False)}"
        )
        llm_event(
            "request_start",
            model=self.settings.openai_model,
            purpose="update_preference_profile",
            feedback_count=len(feedback_context),
            prompt=prompt_summary(prompt),
        )
        return self._complete_text(prompt, purpose="update_preference_profile")

    def summarize_local_novel(self, title: str, chunks: list[str]) -> str:
        if not self.enabled:
            raise RuntimeError("LLM is required to summarize local novels")
        chunk_summaries: list[str] = []
        for index, chunk in enumerate(chunks, 1):
            prompt = (
                "你正在帮助用户从自己喜欢的完本小说中提取阅读偏好。"
                "请只基于给定文本，提取对偏好画像有用的信息。\n"
                "输出中文要点，必须包含：重要人物画像、人物关系、核心冲突、情节走向、题材/关键词、爽点、雷点、文风、节奏、用户可能喜欢它的原因。\n\n"
                f"小说标题：{title}\n"
                f"分块：{index}/{len(chunks)}\n\n"
                f"文本：\n{chunk}"
            )
            llm_event(
                "request_start",
                model=self.settings.openai_model,
                purpose="summarize_local_novel_chunk",
                title=title,
                chunk_index=index,
                chunk_total=len(chunks),
                prompt=prompt_summary(prompt),
            )
            content = self._complete_text(prompt, purpose="summarize_local_novel_chunk", title=title)
            chunk_summaries.append(content)
        if len(chunk_summaries) == 1:
            return chunk_summaries[0]
        prompt = (
            "下面是同一本完本小说不同分块的阅读摘要。请合并为一份去重后的单书偏好摘要，"
            "突出这本书能反映出的用户口味。\n\n"
            f"小说标题：{title}\n\n"
            f"分块摘要：\n{json.dumps(chunk_summaries, ensure_ascii=False)}"
        )
        llm_event(
            "request_start",
            model=self.settings.openai_model,
            purpose="merge_local_novel_summary",
            title=title,
            chunk_total=len(chunks),
            prompt=prompt_summary(prompt),
        )
        return self._complete_text(prompt, purpose="merge_local_novel_summary", title=title)

    def build_initial_profile(self, novel_summaries: list[str]) -> str:
        if not self.enabled:
            raise RuntimeError("LLM is required to build the initial profile")
        prompt = (
            "下面是用户明确喜欢的完本小说摘要。请综合它们生成一份初始用户偏好画像，"
            "用于后续筛选和推荐网络小说。\n"
            "要求：区分强偏好、弱偏好、明确避雷；提炼题材、人物、情节结构、节奏、文风、世界观、爽点与雷点；"
            "不要只罗列书名，要抽象成可用于推荐判断的规则。\n\n"
            f"小说摘要：\n{json.dumps(novel_summaries, ensure_ascii=False)}"
        )
        llm_event(
            "request_start",
            model=self.settings.openai_model,
            purpose="build_initial_profile",
            summary_count=len(novel_summaries),
            prompt=prompt_summary(prompt),
        )
        return self._complete_text(prompt, purpose="build_initial_profile")

    def _complete_text(self, prompt: str, purpose: str, title: str | None = None) -> str:
        try:
            response = self._client().chat.completions.create(
                model=self.settings.openai_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            content = response.choices[0].message.content or ""
            llm_event(
                "response_received",
                model=self.settings.openai_model,
                purpose=purpose,
                title=title,
                raw_response=content,
            )
            if not content.strip():
                raise RuntimeError(f"LLM returned empty response for {purpose}")
            return content.strip()
        except Exception as exc:
            llm_event(
                "request_error",
                "ERROR",
                model=self.settings.openai_model,
                purpose=purpose,
                title=title,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise


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
                recommendation_type="preference",
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
        recommendation_type=_recommendation_type(item.get("recommendation_type")),
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


def _dedupe(seeds: list[SearchSeed]) -> list[SearchSeed]:
    deduped: dict[str, SearchSeed] = {}
    for seed in seeds:
        value = seed.value.strip()
        if value:
            deduped.setdefault(value, SearchSeed(value=value, strategy=seed.strategy))
    return list(deduped.values())


def _take_new(candidates: list[SearchSeed], existing: list[SearchSeed], count: int) -> list[SearchSeed]:
    existing_values = {seed.value for seed in existing}
    result: list[SearchSeed] = []
    for seed in candidates:
        if seed.value in existing_values:
            continue
        result.append(seed)
        existing_values.add(seed.value)
        if len(result) >= count:
            break
    return result


def _recommendation_type(value: object) -> str:
    return "exploration" if str(value).strip() == "exploration" else "preference"
