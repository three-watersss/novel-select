from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .db import Database
from .llm import LLMClient
from .logger import llm_event, workflow_event


MIN_PROFILE_NOVELS = 5
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")
CHAPTER_HEADING = re.compile(
    r"^\s*(?:"
    r"第[零〇一二三四五六七八九十百千万\d]+[章章节回卷集部].{0,40}|"
    r"chapter\s+\d+.{0,40}|"
    r"序章|楔子|引子|正文|尾声|后记|番外.{0,40}"
    r")\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LocalNovel:
    path: Path
    title: str
    text: str


@dataclass(frozen=True)
class NovelChunk:
    title: str
    index: int
    total: int
    text: str


class MissingNovelsError(RuntimeError):
    pass


class InitialProfileError(RuntimeError):
    pass


def list_local_novels(novels_dir: Path) -> list[Path]:
    if not novels_dir.exists():
        return []
    return sorted(path for path in novels_dir.rglob("*.txt") if path.is_file() and path.stat().st_size > 0)


def ensure_minimum_novels(novels_dir: Path, minimum: int = MIN_PROFILE_NOVELS) -> list[Path]:
    paths = list_local_novels(novels_dir)
    if len(paths) < minimum:
        raise MissingNovelsError(
            f"请在 {novels_dir} 添加至少 {minimum} 本你喜欢的完本小说 txt 文件；当前找到 {len(paths)} 本。"
        )
    return paths


def read_local_novel(path: Path) -> LocalNovel:
    last_error: Exception | None = None
    for encoding in ENCODINGS:
        try:
            text = path.read_text(encoding=encoding)
            return LocalNovel(path=path, title=path.stem, text=normalize_text(text))
        except UnicodeDecodeError as exc:
            last_error = exc
    raise InitialProfileError(f"无法读取小说文件 {path}: {last_error}")


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def local_novel_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_size_for_context(context_window: int) -> int:
    reserved = 32_000
    usable = int(max(context_window, 8_000) * 0.55) - reserved
    return max(12_000, usable)


def split_novel_into_chunks(novel: LocalNovel, context_window: int) -> list[NovelChunk]:
    max_chars = chunk_size_for_context(context_window)
    chapters = split_into_chapters(novel.text)
    chunks: list[str] = []
    current = ""
    for chapter in chapters:
        if len(chapter) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(split_long_chapter(chapter, max_chars, novel.title))
            continue
        if current and len(current) + len(chapter) + 2 > max_chars:
            chunks.append(current.strip())
            current = chapter
        else:
            current = f"{current}\n\n{chapter}".strip() if current else chapter
    if current:
        chunks.append(current.strip())
    total = len(chunks)
    return [NovelChunk(title=novel.title, index=index, total=total, text=text) for index, text in enumerate(chunks, 1)]


def split_into_chapters(text: str) -> list[str]:
    lines = text.splitlines()
    chapters: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if CHAPTER_HEADING.match(line) and current:
            chapters.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        chapters.append(current)
    return ["\n".join(chapter).strip() for chapter in chapters if "\n".join(chapter).strip()]


def split_long_chapter(chapter: str, max_chars: int, title: str) -> list[str]:
    workflow_event("long_chapter_split", "WARNING", title=title, chars=len(chapter), max_chars=max_chars)
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", chapter) if paragraph.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(paragraph[i : i + max_chars] for i in range(0, len(paragraph), max_chars))
            continue
        if current and len(current) + len(paragraph) + 2 > max_chars:
            chunks.append(current.strip())
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}".strip() if current else paragraph
    if current:
        chunks.append(current.strip())
    return chunks


class InitialProfileService:
    def __init__(self, db: Database, llm: LLMClient, novels_dir: Path, context_window: int):
        self.db = db
        self.llm = llm
        self.novels_dir = novels_dir
        self.context_window = context_window

    def initialize(self) -> str:
        if self.db.has_preference_events():
            raise InitialProfileError("已经初始化过偏好画像；如需重建请先执行 clear。")
        if not self.llm.enabled:
            raise InitialProfileError("未配置 LLM API key，无法根据本地小说构建初始偏好画像。")

        paths = ensure_minimum_novels(self.novels_dir)
        workflow_event(
            "initial_profile_start",
            novels_dir=self.novels_dir,
            novel_count=len(paths),
            context_window=self.context_window,
            chunk_chars=chunk_size_for_context(self.context_window),
        )
        summaries: list[str] = []
        for path in paths:
            novel = read_local_novel(path)
            content_hash = local_novel_hash(novel.text)
            cached = self.db.cached_local_novel_summary(content_hash, self.context_window)
            if cached is not None:
                summaries.append(cached)
                workflow_event(
                    "local_novel_summary_cache_hit",
                    title=novel.title,
                    path=path,
                    content_hash=content_hash[:12],
                )
                continue
            chunks = split_novel_into_chunks(novel, self.context_window)
            llm_event("novel_summary_start", title=novel.title, path=path, chunks=len(chunks))
            try:
                summary = self.llm.summarize_local_novel(novel.title, [chunk.text for chunk in chunks])
            except Exception as exc:
                raise InitialProfileError(f"生成《{novel.title}》的单书摘要失败：{exc}") from exc
            self.db.save_local_novel_summary(
                content_hash=content_hash,
                context_window=self.context_window,
                path=path,
                title=novel.title,
                text_chars=len(novel.text),
                chunk_count=len(chunks),
                summary=summary,
            )
            summaries.append(summary)
            workflow_event(
                "local_novel_summary_saved",
                title=novel.title,
                path=path,
                content_hash=content_hash[:12],
                chunks=len(chunks),
                chars=len(novel.text),
            )
        try:
            profile = self.llm.build_initial_profile(summaries)
        except Exception as exc:
            raise InitialProfileError(f"生成初始偏好画像失败：{exc}") from exc
        self.db.add_preference_event("initial_profile", profile)
        workflow_event("initial_profile_saved", novel_count=len(paths), profile_chars=len(profile))
        return profile
