"""카드 보강 조사 — "오른 이유 · 백커(투자사·금액)" 를 **구독(claude -p + 웹검색)** 으로. 과금 없음.

강회장 요청(2026-09-21): "오른 이유 / 백커 투자 정도 / 시가총액 / 종합점수 근거 … 매수링크랑".
투자 라운드 무료 API 는 없다(DefiLlama /raises 402, CryptoRank·RootData·Messari 유료) → 웹검색 조사.

환각 방지 (실측에서 잡은 것)
  ZAMA 조사에서 AI 가 누적 투자액을 $398M 으로 적었다. 실제는 $130M(A $73M + B $57M).
  → **총액을 AI 에게 묻지 않는다.** 라운드마다 {라운드·금액·날짜·리드·출처URL} 을 받고, 합계는 코드가 더한다.
  → 출처 URL 이 없는 항목은 버린다. 카드에는 "AI 조사" 표시와 출처 링크를 같이 단다.

비용(구독 사용량) 관리
  - **실제로 보내는 카드에만** 부른다(스캔 전체가 아니라 상위 몇 개).
  - 캐시: 오른 이유 12시간, 백커 30일(투자사는 잘 안 바뀐다).
  - 하루 상한 research_daily_limit.
  - claude CLI 가 없는 곳(로그인 없는 Actions)에서는 조용히 꺼진다 — 카드는 신호 기반 이유만 단다.
    Actions 에서도 켜려면 `claude setup-token` 으로 만든 CLAUDE_CODE_OAUTH_TOKEN 을 secret 으로(구독, 과금 아님).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import shutil
import sys
import tempfile
import time

from providers.claude_news import CREATE_NO_WINDOW, cli_env

from .model import Candidate

log = logging.getLogger("research")

SYSTEM = """You research one crypto token for a Korean alert bot. Use web search.
Report ONLY facts you actually found in a source; never estimate or guess. Every item needs its source URL.
why_up: up to 3 concrete reasons the price moved in the stated window (news, listings, upgrades, flows).
  Empty list if you find nothing specific — do not write generic market commentary.
rounds: each disclosed funding round separately: round name, amount in USD (null if undisclosed),
  date (YYYY-MM), lead investors, source URL. Do NOT sum them. Empty list if none found.
backers: notable VC/fund/angel investors named in sources.
Write reason_ko in Korean, one short sentence each. Output only the requested JSON."""

SCHEMA = {
    "type": "object",
    "properties": {
        "why_up": {"type": "array", "items": {"type": "object", "properties": {
            "reason_ko": {"type": "string"}, "source_url": {"type": "string"}},
            "required": ["reason_ko", "source_url"], "additionalProperties": False}},
        "rounds": {"type": "array", "items": {"type": "object", "properties": {
            "round": {"type": "string"}, "amount_usd": {"type": ["number", "null"]},
            "date": {"type": "string"}, "lead": {"type": "array", "items": {"type": "string"}},
            "source_url": {"type": "string"}},
            "required": ["round", "amount_usd", "date", "lead", "source_url"], "additionalProperties": False}},
        "backers": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["why_up", "rounds", "backers"],
    "additionalProperties": False,
}


def _ok_url(u) -> bool:
    return isinstance(u, str) and u.startswith(("http://", "https://")) and len(u) < 500


def clean(raw: dict) -> dict:
    """출처 없는 항목을 버리고 합계를 코드가 계산한다(AI 가 더한 숫자를 믿지 않는다)."""
    why = [w for w in raw.get("why_up") or [] if w.get("reason_ko") and _ok_url(w.get("source_url"))][:3]
    rounds = [r for r in raw.get("rounds") or [] if _ok_url(r.get("source_url")) and r.get("round")]
    amounts = [r["amount_usd"] for r in rounds if isinstance(r.get("amount_usd"), (int, float)) and r["amount_usd"] > 0]
    rounds.sort(key=lambda r: r.get("date") or "", reverse=True)
    return {"why_up": why, "rounds": rounds, "backers": list(dict.fromkeys(raw.get("backers") or []))[:8],
            "total_raised_usd": sum(amounts) if amounts else None,
            "undisclosed_rounds": sum(1 for r in rounds if not r.get("amount_usd"))}


class Researcher:
    def __init__(self, settings, store):
        self.s, self.store = settings, store
        self.exe = shutil.which("claude")
        self.model = settings.get("research.model", "haiku")

    @property
    def enabled(self) -> bool:
        return bool(self.s.get("research.enabled", True)) and self.exe is not None

    def _budget(self) -> bool:
        return self.store.research_calls(dt.date.today()) < int(self.s.get("research.daily_limit", 30))

    async def _ask(self, prompt: str) -> dict | None:
        kw = {"creationflags": CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        args = ["-p", prompt, "--model", self.model, "--output-format", "json", "--no-session-persistence",
                "--disable-slash-commands", "--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch", "WebFetch",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--system-prompt", SYSTEM, "--json-schema", json.dumps(SCHEMA)]
        proc = await asyncio.create_subprocess_exec(self.exe, *args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, env=cli_env(),
                                                    cwd=tempfile.gettempdir(), **kw)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=float(self.s.get("research.timeout_sec", 300)))
        except asyncio.TimeoutError:
            proc.kill()
            return None
        try:
            d = json.loads(out.decode("utf-8", "replace"))
        except ValueError:
            return None
        self.store.log_research(float(d.get("total_cost_usd") or 0))
        if d.get("is_error") or d.get("subtype") != "success":
            return None
        return d.get("structured_output")

    async def enrich(self, c: Candidate) -> None:
        """캐시에 있으면 쓰고, 없거나 낡았으면 조사한다. 결과는 c.research 에."""
        cached = self.store.research_get(c.coin_key)
        now = time.time()
        why_fresh = cached and now - cached["ts_why"] < float(self.s.get("research.why_ttl_h", 12)) * 3600
        fund_fresh = cached and now - cached["ts_fund"] < float(self.s.get("research.fund_ttl_d", 30)) * 86400
        if why_fresh and fund_fresh:
            c.research = cached["data"]
            return
        if not self.enabled or not self._budget():
            c.research = cached["data"] if cached else None
            return
        ch = f"24h {c.change_24h:+.1f}%" if c.change_24h is not None else ""
        name = f"{c.name} ({c.symbol})" if c.name and c.name != c.symbol else c.symbol
        where = f", contract {c.chain}:{c.address}" if c.address else (f", CoinGecko id {c.cg_id}" if c.cg_id else "")
        prompt = (f"Token: {name}{where}. {ch}. Today is {dt.date.today().isoformat()}. "
                  f"Why did it move in the last 3 days, and who funded the project (rounds)?")
        raw = await self._ask(prompt)
        if raw is None:
            c.research = cached["data"] if cached else None
            return
        data = clean(raw)
        self.store.research_put(c.coin_key, data)
        c.research = data


async def enrich_many(cands: list[Candidate], researcher: Researcher | None, top: int) -> None:
    if researcher is None:
        return
    for c in cands[:top]:          # 순서대로 — 동시에 여러 개 띄우면 구독 한도를 한꺼번에 쓴다
        try:
            await researcher.enrich(c)
        except Exception as e:  # noqa: BLE001
            log.warning("조사 실패 %s: %s", c.symbol, e)
