from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx


@dataclass
class FetchResult:
    url: str
    text: str
    content_type: str

    def json(self) -> object:
        return json.loads(self.text)


class HttpClient:
    def __init__(self, timeout: float, user_agent: str):
        self.timeout = timeout
        self.user_agent = user_agent

    def get(self, url: str, base_url: str | None = None, headers: dict | None = None) -> FetchResult:
        final_url = urljoin(base_url, url) if base_url else url
        merged_headers = {"User-Agent": self.user_agent}
        if headers:
            merged_headers.update(headers)
        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers=merged_headers) as client:
            response = client.get(final_url)
            response.raise_for_status()
            response.encoding = response.encoding or "utf-8"
            return FetchResult(
                url=str(response.url),
                text=response.text,
                content_type=response.headers.get("content-type", ""),
            )

