"""GitHub API client. Public data works without a token; a token only raises rate limits."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from uaskills.errors import GitHubError
from uaskills.textutil import redact

ALLOWED_HOSTS = {"api.github.com", "raw.githubusercontent.com", "codeload.githubusercontent.com"}


class GitHubClient:
    def __init__(
        self,
        *,
        token: str | None = None,
        api_base: str = "https://api.github.com",
        raw_base: str = "https://raw.githubusercontent.com",
        user_agent: str = "universal-ai-skills/0.1",
        min_interval: float = 1.0,
        raw_min_interval: float | None = None,
        timeout: float = 30,
        max_retries: int = 3,
        opener: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.raw_base = raw_base.rstrip("/")
        self.user_agent = user_agent
        self.min_interval = min_interval
        self.raw_min_interval = min_interval if raw_min_interval is None else raw_min_interval
        self.timeout = timeout
        self.max_retries = max_retries
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep
        self._clock = clock
        self._next_at = 0.0
        self._next_raw = 0.0
        self._lock = threading.Lock()
        self.last_status: int | None = None
        self.rate_remaining: int | None = None
        self.rate_exhausted = False

    def _pace(self, host: str | None = None) -> None:
        raw = host not in {None, "api.github.com"}
        with self._lock:
            if self.rate_exhausted and not raw:
                raise GitHubError(
                    429,
                    "GitHub rate limit is nearly exhausted. Resume later with the same command and --resume.",
                )
            now = self._clock()
            if raw:
                wait = max(0.0, self._next_raw - now)
                self._next_raw = max(self._next_raw, now) + self.raw_min_interval
            else:
                wait = max(0.0, self._next_at - now)
                self._next_at = max(self._next_at, now) + self.min_interval
        if wait:
            self._sleep(wait)

    def _request(self, url: str, accept: str = "application/vnd.github+json") -> tuple[int, bytes, dict[str, str]]:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            raise GitHubError(0, f"Refusing URL outside the GitHub hosts: {parsed.hostname}")
        headers = {
            "Accept": accept,
            "User-Agent": self.user_agent,
        }
        if self.token and parsed.hostname == "api.github.com":
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        attempt = 0
        while True:
            self._pace(parsed.hostname)
            attempt += 1
            try:
                with self._open(request, timeout=self.timeout) as response:
                    status = getattr(response, "status", 200)
                    payload = response.read()
                    header_map = {key: value for key, value in response.headers.items()}
                    self._note_rate(header_map, status)
                    return status, payload, header_map
            except urllib.error.HTTPError as exc:
                body = exc.read() if exc.fp else b""
                header_map = {key: value for key, value in exc.headers.items()} if exc.headers else {}
                self._note_rate(header_map, exc.code)
                if exc.code in {502, 503, 504} and attempt <= self.max_retries:
                    self._sleep(min(30.0, 2.0 * attempt))
                    continue
                if exc.code == 403 and attempt <= self.max_retries:
                    retry_after = _retry_after(header_map)
                    if retry_after is not None and retry_after <= 60:
                        self._sleep(retry_after)
                        continue
                message = _error_message(exc.code, body, header_map)
                raise GitHubError(exc.code, message, _retry_after(header_map)) from exc
            except urllib.error.URLError as exc:
                if attempt <= self.max_retries:
                    self._sleep(min(10.0, attempt))
                    continue
                raise GitHubError(0, redact(f"network error: {exc.reason}")) from exc

    def _note_rate(self, headers: dict[str, str], status: int) -> None:
        with self._lock:
            self.last_status = status
            remaining = headers.get("X-RateLimit-Remaining")
            if remaining is not None and str(remaining).isdigit():
                self.rate_remaining = int(remaining)
                if self.rate_remaining <= 1:
                    self.rate_exhausted = True

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = path if path.startswith("https://") else self.api_base + path
        if params:
            query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
            url = url + ("&" if "?" in url else "?") + query
        _status, payload, _headers = self._request(url)
        if not payload:
            return None
        try:
            return json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise GitHubError(0, "GitHub returned a response that was not JSON") from exc

    def get_bytes(self, url: str, max_bytes: int) -> bytes:
        _status, payload, _headers = self._request(url, accept="application/octet-stream")
        if len(payload) > max_bytes:
            raise GitHubError(0, f"download exceeded {max_bytes} bytes")
        return payload

    def repo(self, owner: str, repo: str) -> dict[str, Any]:
        data = self.get_json(f"/repos/{owner}/{repo}")
        if not isinstance(data, dict):
            raise GitHubError(0, "repository metadata was not an object")
        return data

    def commit_sha(self, owner: str, repo: str, branch: str) -> str:
        data = self.get_json(f"/repos/{owner}/{repo}/commits/{urllib.parse.quote(branch)}")
        sha = data.get("sha") if isinstance(data, dict) else None
        if not isinstance(sha, str) or len(sha) < 7:
            raise GitHubError(0, "commit response did not include a sha")
        return sha

    def tree(self, owner: str, repo: str, sha: str) -> dict[str, Any]:
        data = self.get_json(f"/repos/{owner}/{repo}/git/trees/{sha}", {"recursive": "1"})
        if not isinstance(data, dict):
            raise GitHubError(0, "tree response was not an object")
        return data

    def list_contents(self, owner: str, repo: str, path: str, ref: str) -> list[dict[str, Any]]:
        quoted = urllib.parse.quote(path.strip("/"))
        suffix = f"/repos/{owner}/{repo}/contents/{quoted}" if quoted else f"/repos/{owner}/{repo}/contents"
        data = self.get_json(suffix, {"ref": ref})
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    def file_bytes(self, owner: str, repo: str, sha: str, path: str, max_bytes: int) -> bytes:
        quoted = "/".join(urllib.parse.quote(part) for part in path.split("/") if part)
        url = f"{self.raw_base}/{owner}/{repo}/{sha}/{quoted}"
        return self.get_bytes(url, max_bytes)

    def search_repositories(self, query: str, page: int, per_page: int) -> dict[str, Any]:
        data = self.get_json(
            "/search/repositories",
            {"q": query, "page": page, "per_page": per_page, "sort": "updated"},
        )
        if not isinstance(data, dict):
            raise GitHubError(0, "search response was not an object")
        return data

    def list_org_repos(self, org: str, page: int, per_page: int) -> tuple[list[dict[str, Any]], bool]:
        data = self.get_json(f"/orgs/{urllib.parse.quote(org)}/repos", {"page": page, "per_page": per_page, "type": "public"})
        if not isinstance(data, list):
            return [], False
        return [item for item in data if isinstance(item, dict)], len(data) == per_page


def _retry_after(headers: dict[str, str]) -> float | None:
    raw = headers.get("Retry-After")
    if raw and str(raw).replace(".", "", 1).isdigit():
        return float(raw)
    return None


def _error_message(status: int, body: bytes, headers: dict[str, str]) -> str:
    detail = ""
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace") or "{}")
        if isinstance(parsed, dict) and parsed.get("message"):
            detail = str(parsed["message"])
    except json.JSONDecodeError:
        detail = ""
    if status == 403 and headers.get("X-RateLimit-Remaining") == "0":
        detail = detail or "rate limit exceeded"
    return redact(f"GitHub HTTP {status}" + (f": {detail}" if detail else ""))
