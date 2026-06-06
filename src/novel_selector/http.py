from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from .logger import source_event


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
        return self.request("GET", final_url, headers=headers)

    def post(
        self,
        url: str,
        base_url: str | None = None,
        headers: dict | None = None,
        data: str | dict | None = None,
    ) -> FetchResult:
        final_url = urljoin(base_url, url) if base_url else url
        return self.request("POST", final_url, headers=headers, data=data)

    def request(self, method: str, url: str, headers: dict | None = None, data: str | dict | None = None) -> FetchResult:
        merged_headers = {"User-Agent": self.user_agent}
        if headers:
            merged_headers.update(headers)
        started = time.monotonic()
        source_event("request_start", method=method.upper(), url=url)
        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers=merged_headers) as client:
            try:
                response = client.request(method.upper(), url, data=data)
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                source_event(
                    "request_timeout",
                    "WARNING",
                    method=method.upper(),
                    url=url,
                    timeout=self.timeout,
                    duration_seconds=round(time.monotonic() - started, 3),
                    error=str(exc),
                )
                raise
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                source_event(
                    "request_failed",
                    "WARNING" if status_code == 403 else "ERROR",
                    method=method.upper(),
                    url=url,
                    status_code=status_code,
                    reason="forbidden" if status_code == 403 else "http_status",
                    duration_seconds=round(time.monotonic() - started, 3),
                )
                raise
            except httpx.HTTPError as exc:
                source_event(
                    "request_failed",
                    "ERROR",
                    method=method.upper(),
                    url=url,
                    reason="http_error",
                    duration_seconds=round(time.monotonic() - started, 3),
                    error=str(exc),
                )
                raise
            response.encoding = response.encoding or "utf-8"
            source_event(
                "request_end",
                url=str(response.url),
                status_code=response.status_code,
                content_type=response.headers.get("content-type", ""),
                bytes=len(response.content),
                duration_seconds=round(time.monotonic() - started, 3),
            )
            return FetchResult(
                url=str(response.url),
                text=response.text,
                content_type=response.headers.get("content-type", ""),
            )
