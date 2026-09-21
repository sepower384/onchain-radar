"""모든 외부 API 호출의 공통 입구 — 레이트리밋 · 캐시 · 백오프 재시도 · 실패 격리.

사양서 규칙: "키가 없거나 실패하면 해당 신호만 비활성화되고 전체는 계속 돌아가게 한다."
그래서 여기서는 **절대 예외를 위로 던지지 않는다.** 실패하면 None 을 돌려주고 로그만 남긴다.
호출하는 쪽은 None 을 "그 신호 없음"으로 받아 재정규화한다.

실측에서 만난 상태 코드
  402  DefiLlama /emissions — 유료 전환 요구. 재시도해도 소용없으므로 곧장 포기한다.
  429  CoinGecko 키 없이 연속 호출 — Retry-After 를 지켜 기다린다.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from typing import Any

import httpx

log = logging.getLogger("provider")

UA = "onchain-radar/0.1 (+telegram bot)"
NO_RETRY = {400, 401, 402, 403, 404, 422}     # 다시 해도 결과가 같은 것들


class RateLimiter:
    """분당 N회. 공급자마다 하나씩 둔다."""

    def __init__(self, per_min: int):
        self.per_min = max(1, per_min)
        self.hits: deque[float] = deque()
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                while self.hits and now - self.hits[0] > 60:
                    self.hits.popleft()
                if len(self.hits) < self.per_min:
                    self.hits.append(now)
                    return
                await asyncio.sleep(60 - (now - self.hits[0]) + 0.05)


class TTLCache:
    def __init__(self) -> None:
        self.data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any:
        hit = self.data.get(key)
        if not hit:
            return None
        exp, val = hit
        if time.monotonic() > exp:
            self.data.pop(key, None)
            return None
        return val

    def put(self, key: str, val: Any, ttl: float) -> None:
        if ttl > 0:
            self.data[key] = (time.monotonic() + ttl, val)


class Provider:
    """공급자 공통 부모. 하위 클래스는 name · base_url 만 정하면 된다."""

    name = "base"
    base_url = ""
    default_headers: dict[str, str] = {}

    def __init__(self, settings, client: httpx.AsyncClient | None = None):
        self.s = settings
        self.client = client
        self._own_client = client is None
        rate = settings.get(f"providers.rate_per_min.{self.name}", 60)
        self.limiter = RateLimiter(int(rate))
        self.cache = TTLCache()
        self.timeout = float(settings.get("providers.timeout_sec", 20))
        self.retries = int(settings.get("providers.retries", 3))
        self.backoff = float(settings.get("providers.backoff_base_sec", 1.5))
        self.stats = {"ok": 0, "fail": 0, "cache": 0}
        self.last_error: str | None = None

    # 하위 클래스가 키 여부로 스스로를 끌 수 있다
    @property
    def enabled(self) -> bool:
        return True

    async def _client(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(
                timeout=self.timeout, headers={"User-Agent": UA}, follow_redirects=True,
                # 윈도우 사내 인증서 환경에서 검증이 깨지는 일이 잦다(강회장 PC 실측).
                verify=False)
        return self.client

    async def close(self) -> None:
        if self._own_client and self.client is not None:
            await self.client.aclose()
            self.client = None

    async def get_json(self, path: str, params: dict | None = None, *, ttl: float = 0,
                       headers: dict | None = None, cache_key: str | None = None) -> Any:
        """성공하면 JSON, 실패하면 None. 예외는 여기서 끝난다."""
        if not self.enabled:
            return None
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        ck = cache_key or f"{url}?{sorted((params or {}).items())}"
        cached = self.cache.get(ck)
        if cached is not None:
            self.stats["cache"] += 1
            return cached

        h = {**self.default_headers, **(headers or {})}
        client = await self._client()
        for attempt in range(1, self.retries + 1):
            await self.limiter.wait()
            try:
                r = await client.get(url, params=params, headers=h)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                self.last_error = f"{type(e).__name__}"
                await self._sleep(attempt)
                continue

            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    self.last_error = "JSON 아님"
                    break
                self.cache.put(ck, data, ttl)
                self.stats["ok"] += 1
                return data

            self.last_error = f"HTTP {r.status_code}"
            if r.status_code in NO_RETRY:
                break
            if r.status_code == 429:
                ra = r.headers.get("Retry-After")
                wait = float(ra) if ra and ra.replace(".", "").isdigit() else self.backoff * 4 * attempt
                log.warning("%s 429 — %.1f초 대기 (%s)", self.name, wait, url)
                await asyncio.sleep(min(wait, 60))
                continue
            await self._sleep(attempt)

        self.stats["fail"] += 1
        log.warning("%s 실패: %s (%s)", self.name, self.last_error, url)
        return None

    async def post_json(self, path: str, body: dict, *, ttl: float = 0, headers: dict | None = None,
                        cache_key: str | None = None) -> Any:
        """POST 판 get_json — 같은 규칙(레이트리밋·캐시·재시도·예외 격리)."""
        if not self.enabled:
            return None
        import json as _json
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        ck = cache_key or f"POST {url} {_json.dumps(body, sort_keys=True)}"
        cached = self.cache.get(ck)
        if cached is not None:
            self.stats["cache"] += 1
            return cached
        client = await self._client()
        h = {**self.default_headers, "Content-Type": "application/json", **(headers or {})}
        for attempt in range(1, self.retries + 1):
            await self.limiter.wait()
            try:
                r = await client.post(url, json=body, headers=h)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                self.last_error = type(e).__name__
                await self._sleep(attempt)
                continue
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    self.last_error = "JSON 아님"
                    break
                self.cache.put(ck, data, ttl)
                self.stats["ok"] += 1
                return data
            self.last_error = f"HTTP {r.status_code}"
            if r.status_code in NO_RETRY:
                break
            await self._sleep(attempt)
        self.stats["fail"] += 1
        log.warning("%s POST 실패: %s (%s)", self.name, self.last_error, url)
        return None

    async def _sleep(self, attempt: int) -> None:
        await asyncio.sleep(self.backoff * (2 ** (attempt - 1)) + random.uniform(0, 0.4))


def fnum(v: Any, default: float | None = None) -> float | None:
    """API 들이 숫자를 문자열로 준다(비트겟·GoPlus). 빈 문자열·None 은 default."""
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
