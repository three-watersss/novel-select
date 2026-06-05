from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup, Tag
from jsonpath_ng import parse as parse_jsonpath
from lxml import html as lxml_html

from .db import source_id
from .http import HttpClient
from .logger import source_event
from .models import Chapter, NovelCandidate
from .text import clean_text, is_explicitly_complete


UNSUPPORTED_MARKERS = ("@js:", "<js>", "webView", "java.", "captcha", "验证码")


class UnsupportedSourceError(RuntimeError):
    pass


class RuleEvaluationError(RuntimeError):
    pass


def source_support_status(source: dict) -> tuple[bool, str]:
    if not source.get("searchUrl"):
        return False, "missing_search_url"
    if not source.get("ruleSearch"):
        return False, "missing_rule_search"
    search_dump = json.dumps(
        {
            "searchUrl": source.get("searchUrl"),
            "ruleSearch": source.get("ruleSearch"),
            "header": source.get("header"),
        },
        ensure_ascii=False,
    )
    if any(marker in search_dump for marker in UNSUPPORTED_MARKERS):
        return False, "contains_js_webview_or_captcha_search_rule"
    return True, "supported"


def render_url(template: str, key: str = "", page: int = 1) -> str:
    if template.strip().startswith("@js") or "<js>" in template:
        raise UnsupportedSourceError("JS URL templates are not supported in v1")
    value = template
    value = value.replace("{{key}}", quote(key))
    value = value.replace("{{page}}", str(page))
    value = re.sub(r"\{\{\s*key\s*\}\}", quote(key), value)
    value = re.sub(r"\{\{\s*page\s*\}\}", str(page), value)
    if "," in value and value.lstrip().startswith(("http://", "https://", "/")):
        value = value.split(",", 1)[0]
    return value.strip()


class LegadoClient:
    def __init__(self, http: HttpClient):
        self.http = http

    def search(self, source: dict, keyword: str, page: int = 1) -> list[NovelCandidate]:
        sid = source_id(source)
        source_name = source.get("bookSourceName") or sid
        supported, reason = source_support_status(source)
        if not supported:
            source_event(
                "unsupported_source",
                "WARNING",
                source_id=sid,
                source_name=source_name,
                stage="search",
                reason=reason,
            )
            raise UnsupportedSourceError(reason)
        base_url = source.get("bookSourceUrl", "")
        url = render_url(str(source["searchUrl"]), keyword, page)
        source_event(
            "search_start",
            source_id=sid,
            source_name=source_name,
            keyword=keyword,
            page=page,
            url=url,
            base_url=base_url,
        )
        fetched = self.http.get(url, base_url=base_url, headers=_headers(source))
        rule = source.get("ruleSearch") or {}
        items = evaluate_list(fetched.text, fetched.content_type, rule.get("bookList", ""))
        candidates: list[NovelCandidate] = []
        for item in items:
            title = clean_text(evaluate_value(item, rule.get("name", "")))
            author = clean_text(evaluate_value(item, rule.get("author", "")))
            book_url = clean_text(evaluate_value(item, rule.get("bookUrl", "")))
            if not title or not author or not is_usable_url_value(book_url):
                continue
            kind = clean_text(evaluate_value(item, rule.get("kind", "")))
            intro = clean_text(evaluate_value(item, rule.get("intro", "")))
            latest = clean_text(evaluate_value(item, rule.get("lastChapter", "")))
            word_count = clean_text(evaluate_value(item, rule.get("wordCount", "")))
            completed = is_explicitly_complete(kind, intro, latest)
            candidates.append(
                NovelCandidate(
                    title=title,
                    author=author,
                    source_id=sid,
                    source_name=source.get("bookSourceName") or sid,
                    book_url=urljoin(fetched.url, book_url),
                    intro=intro,
                    kind=kind,
                    latest_chapter=latest,
                    word_count=word_count,
                    cover_url=clean_text(evaluate_value(item, rule.get("coverUrl", ""))),
                    completed=completed,
                    completion_evidence=kind or latest or intro,
                    raw={
                        "keyword": keyword,
                        "page": page,
                        "fetched_url": fetched.url,
                        "source": source.get("bookSourceName"),
                    },
                )
            )
        source_event(
            "search_parsed",
            source_id=sid,
            source_name=source_name,
            keyword=keyword,
            fetched_url=fetched.url,
            raw_items=len(items),
            candidates=len(candidates),
        )
        return candidates

    def enrich_book_info(self, source: dict, candidate: NovelCandidate) -> NovelCandidate:
        rule = source.get("ruleBookInfo") or {}
        if not rule:
            return candidate
        sid = source_id(source)
        source_name = source.get("bookSourceName") or sid
        source_event(
            "book_info_start",
            source_id=sid,
            source_name=source_name,
            title=candidate.title,
            book_url=candidate.book_url,
        )
        fetched = self.http.get(candidate.book_url, headers=_headers(source))
        root: Any = fetched.text
        kind = clean_text(evaluate_value(root, rule.get("kind", ""))) or candidate.kind
        intro = clean_text(evaluate_value(root, rule.get("intro", ""))) or candidate.intro
        latest = clean_text(evaluate_value(root, rule.get("lastChapter", ""))) or candidate.latest_chapter
        candidate.kind = kind
        candidate.intro = intro
        candidate.latest_chapter = latest
        candidate.word_count = clean_text(evaluate_value(root, rule.get("wordCount", ""))) or candidate.word_count
        candidate.cover_url = clean_text(evaluate_value(root, rule.get("coverUrl", ""))) or candidate.cover_url
        candidate.completed = is_explicitly_complete(kind, intro, latest)
        candidate.completion_evidence = kind or latest or intro
        toc_url = clean_text(evaluate_value(root, rule.get("tocUrl", "")))
        if toc_url:
            candidate.raw["toc_url"] = urljoin(fetched.url, toc_url)
        source_event(
            "book_info_parsed",
            source_id=sid,
            source_name=source_name,
            title=candidate.title,
            completed=candidate.completed,
            has_intro=bool(candidate.intro),
            has_toc_url=bool(toc_url),
        )
        return candidate

    def chapters(self, source: dict, candidate: NovelCandidate, limit: int = 10) -> list[Chapter]:
        rule = source.get("ruleToc") or {}
        if not rule:
            source_event(
                "unsupported_source",
                "WARNING",
                source_id=source_id(source),
                source_name=source.get("bookSourceName") or source_id(source),
                stage="toc",
                reason="missing_rule_toc",
                title=candidate.title,
            )
            raise UnsupportedSourceError("missing_rule_toc")
        toc_url = candidate.raw.get("toc_url") or candidate.book_url
        sid = source_id(source)
        source_name = source.get("bookSourceName") or sid
        source_event("toc_start", source_id=sid, source_name=source_name, title=candidate.title, url=toc_url)
        fetched = self.http.get(toc_url, headers=_headers(source))
        items = evaluate_list(fetched.text, fetched.content_type, rule.get("chapterList", ""))
        chapters: list[Chapter] = []
        for item in items[:limit]:
            title = clean_text(evaluate_value(item, rule.get("chapterName", "")))
            url = clean_text(evaluate_value(item, rule.get("chapterUrl", "")))
            if not title or not url:
                continue
            chapters.append(Chapter(title=title, url=urljoin(fetched.url, url)))
        source_event(
            "toc_parsed",
            source_id=sid,
            source_name=source_name,
            title=candidate.title,
            raw_items=len(items),
            chapters=len(chapters),
        )
        return chapters

    def chapter_content(self, source: dict, chapter: Chapter) -> str:
        rule = source.get("ruleContent") or {}
        if not rule:
            source_event(
                "unsupported_source",
                "WARNING",
                source_id=source_id(source),
                source_name=source.get("bookSourceName") or source_id(source),
                stage="content",
                reason="missing_rule_content",
                chapter_title=chapter.title,
            )
            raise UnsupportedSourceError("missing_rule_content")
        sid = source_id(source)
        source_name = source.get("bookSourceName") or sid
        source_event("content_start", source_id=sid, source_name=source_name, chapter_title=chapter.title, url=chapter.url)
        fetched = self.http.get(chapter.url, headers=_headers(source))
        content = clean_text(evaluate_value(fetched.text, rule.get("content", "")))
        replace_regex = rule.get("replaceRegex")
        if replace_regex:
            content = _apply_replace_regex(content, replace_regex)
        source_event(
            "content_parsed",
            source_id=sid,
            source_name=source_name,
            chapter_title=chapter.title,
            chars=len(content),
            empty=not bool(content),
        )
        return content


def evaluate_list(document: Any, content_type: str, rule: str) -> list[Any]:
    if not rule:
        return []
    rule = strip_unsupported_tail(str(rule))
    if "||" in rule:
        for option in [part.strip() for part in rule.split("||") if part.strip()]:
            values = evaluate_list(document, content_type, option)
            if values:
                return values
        return []
    parsed = maybe_json(document, content_type)
    if parsed is not None:
        json_rule = normalize_json_rule(rule)
        if json_rule:
            values = [m.value for m in parse_jsonpath(json_rule).find(parsed)]
            if len(values) == 1 and isinstance(values[0], list):
                return values[0]
            return values
    if rule.startswith("//") or rule.startswith("("):
        tree = lxml_html.fromstring(document)
        return tree.xpath(rule)
    soup = BeautifulSoup(document, "lxml")
    return list(evaluate_html_rule(soup, rule, want_list=True))


def evaluate_value(context: Any, rule: str) -> str:
    if not rule:
        return ""
    rule = strip_unsupported_tail(str(rule))
    if "||" in rule:
        for option in [part.strip() for part in rule.split("||") if part.strip()]:
            value = evaluate_value(context, option)
            if value:
                return value
        return ""
    parsed = context if isinstance(context, (dict, list)) else maybe_json(context, "")
    if parsed is not None:
        interpolated = interpolate_json_rule(parsed, rule)
        if interpolated != rule:
            return clean_text(interpolated)
        json_rule = normalize_json_rule(rule)
        if json_rule:
            matches = [m.value for m in parse_jsonpath(json_rule).find(parsed)]
            return clean_text(matches[0]) if matches else ""
    if isinstance(context, str) and (rule.startswith("//") or rule.startswith("(")):
        tree = lxml_html.fromstring(context)
        values = tree.xpath(rule)
        return clean_text(values[0]) if values else ""
    if hasattr(context, "xpath") and (rule.startswith(".//") or rule.startswith("//")):
        values = context.xpath(rule)
        return clean_text(values[0]) if values else ""
    if isinstance(context, str):
        context = BeautifulSoup(context, "lxml")
    if isinstance(context, (BeautifulSoup, Tag)):
        values = list(evaluate_html_rule(context, rule, want_list=False))
        return clean_text(values[0]) if values else ""
    return clean_text(context)


def evaluate_html_rule(context: BeautifulSoup | Tag, rule: str, want_list: bool) -> list[Any]:
    parts = [p.strip() for p in re.split(r"@|&&", rule) if p.strip()]
    current: list[Any] = [context]
    for part in parts:
        if part in {"text", "textNodes"}:
            current = [clean_text(x.get_text(" ") if isinstance(x, Tag) else x) for x in current]
            continue
        if part in {"html", "all"}:
            current = [str(x) for x in current]
            continue
        if part.startswith("attr."):
            attr = part.split(".", 1)[1]
            current = [_attr(x, attr) for x in current if isinstance(x, Tag)]
            continue
        if part in {"href", "src"}:
            current = [_attr(x, part) for x in current if isinstance(x, Tag)]
            continue
        selected: list[Any] = []
        for item in current:
            if not isinstance(item, (BeautifulSoup, Tag)):
                continue
            selected.extend(select_html(item, part))
        current = selected
    if not want_list and current:
        return [current[0]]
    return current


def select_html(context: BeautifulSoup | Tag, rule: str) -> list[Tag]:
    index: int | None = None
    rule = re.sub(r"!\d+$", "", rule)
    match = re.match(r"(.+)\.(\d+)$", rule)
    if match:
        rule = match.group(1)
        index = int(match.group(2))
    if rule.startswith("class."):
        selector = "." + rule.split(".", 1)[1]
    elif rule.startswith("id."):
        selector = "#" + rule.split(".", 1)[1]
    elif rule.startswith("tag."):
        selector = rule.split(".", 1)[1]
    elif rule.startswith("@css:"):
        selector = rule.removeprefix("@css:")
    else:
        selector = rule
    found = context.select(selector)
    if index is not None:
        return [found[index]] if 0 <= index < len(found) else []
    return found


def maybe_json(document: Any, content_type: str) -> Any | None:
    if isinstance(document, (dict, list)):
        return document
    if not isinstance(document, str):
        return None
    stripped = document.lstrip()
    if "json" not in content_type and not stripped.startswith(("{", "[")):
        return None
    try:
        return json.loads(document)
    except json.JSONDecodeError:
        return None


def normalize_json_rule(rule: str) -> str:
    rule = rule.strip()
    if not rule:
        return ""
    rule = rule.split("@", 1)[0].split("##", 1)[0].strip()
    if not rule:
        return ""
    if rule.startswith("$"):
        return rule
    if re.fullmatch(r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*(?:\[\*\])?", rule):
        return "$." + rule
    return ""


def interpolate_json_rule(data: Any, rule: str) -> str:
    def repl(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        if not expr.startswith("$"):
            expr = "$." + expr
        values = [m.value for m in parse_jsonpath(expr).find(data)]
        if not values:
            return ""
        value = values[0]
        if isinstance(value, list):
            return ",".join(clean_text(v) for v in value)
        return clean_text(value)

    return re.sub(r"\{\{?\s*(\$?\.?[\w.\[\]*]+)\s*\}?\}", repl, rule)


def strip_unsupported_tail(rule: str) -> str:
    for marker in ("@js:", "<js>"):
        if marker in rule:
            rule = rule.split(marker, 1)[0]
    return rule.strip()


def _headers(source: dict) -> dict:
    raw = source.get("header")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}


def _attr(tag: Tag, attr: str) -> str:
    value = tag.get(attr, "")
    if isinstance(value, list):
        return " ".join(value)
    return str(value)


def _apply_replace_regex(text: str, spec: str) -> str:
    # Legado replaceRegex is expressive; v1 supports plain regex removal.
    try:
        return re.sub(spec, "", text)
    except re.error:
        return text


def is_usable_url_value(value: str) -> bool:
    if not value:
        return False
    if "{" in value or "}" in value:
        return False
    if value.endswith(("?", "&")):
        return False
    if re.search(r"(?:resourceId|book_id|bookId|id)=$", value):
        return False
    return True


def candidate_to_dict(candidate: NovelCandidate) -> dict:
    return asdict(candidate)
