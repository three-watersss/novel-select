from __future__ import annotations

import hashlib
import re
from html import unescape


COMPLETE_TERMS = ("完结", "完本", "已完成", "已完结", "Completed", "completed", "finish", "finished")
INCOMPLETE_TERMS = ("连载", "未完", "更新", "ongoing", "serial")


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_name(value: str) -> str:
    text = clean_text(value).lower()
    text = re.sub(r"[《》【】\[\]（）()「」『』,，。.!！?？:：;；\s_-]+", "", text)
    return text


def novel_fingerprint(title: str, author: str) -> str:
    raw = f"{normalize_name(title)}::{normalize_name(author)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_explicitly_complete(*values: object) -> bool:
    text = " ".join(clean_text(v) for v in values if v)
    if not text:
        return False
    if any(term in text for term in INCOMPLETE_TERMS):
        return False
    return any(term in text for term in COMPLETE_TERMS)


def truncate_chars(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n[TRUNCATED]"

