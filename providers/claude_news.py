"""Stage 6 뉴스 분류기 — 헤드라인을 '실질 호재 / 노이즈'로 가른다. **API 과금 없이 구독으로.**

강회장 지시(2026-09-21): "과금없이 구독에서 해결". 그래서 Anthropic API 를 부르지 않고,
이 PC 에 로그인된 **Claude Code CLI(`claude -p`)** 를 헤드리스로 부른다 = 구독 사용량.

실측으로 정한 호출 방식 (claude 2.1.278)
  claude -p <헤드라인 묶음> --model haiku --output-format json --json-schema <스키마>
         --system-prompt <분류 기준> --tools "" --no-session-persistence --disable-slash-commands
         --strict-mcp-config --mcp-config {"mcpServers":{}}
  환경: ANTHROPIC_API_KEY · ANTHROPIC_AUTH_TOKEN 제거(남아 있으면 **API 로 과금**된다),
        CLAUDE_CODE_DISABLE_AUTO_MEMORY=1
  - `--bare` 금지: OAuth 로그인을 안 읽어서 구독으로 못 돈다.
  - MCP 를 안 끄면 연결된 도구 정의(Gmail·Slack·Notion…)가 실려 한 번에 6.8만 토큰. 끄면 약 5천(−90%).
  - 분류 기준(SYSTEM)이 없으면 Haiku 가 "바이낸스 ZAMA 상장"을 노이즈로 오분류했다. 넣으면 10/10.
  - 결과는 응답 JSON 의 `structured_output` 에 스키마대로 온다.
  - 봇이 창 없이(pythonw) 돌므로 CLI 도 CREATE_NO_WINDOW 로 띄운다 — 콘솔이 깜빡이면 안 된다.

규칙으로 가를 수 있는 거래소 공식 공지는 여기 오지 않는다(scanners/news_rules.py). 일반 뉴스만.
하루 호출 상한(daily_call_limit)은 구독 사용량을 지키려고 둔다.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import shutil
import sys
import tempfile

log = logging.getLogger("claude_news")

SYSTEM = """You classify crypto news headlines for a Korean on-chain tracking bot.
The bot only surfaces news that is a *verifiable, material* positive catalyst for a specific token.

REAL (material catalyst) — only these categories:
- cex_listing: a major exchange officially confirms SPOT listing (not futures, not "considering")
- usage_revenue: measurable rise in protocol revenue, fees, users, or TVL, with numbers
- mainnet_upgrade: a mainnet or major protocol upgrade actually shipped (not "planned")
- partnership: a partnership the counterparty has also announced publicly (name the counterparty)
- burn_buyback: a token burn or buyback that was executed (not proposed)
- regulatory: a concrete regulatory approval (ETF approval, license granted, investigation closed)

NOISE: talks/discussions, unsourced rumors, AMAs, influencer mentions, price-prediction articles,
roadmap restatements, "could"/"may"/"plans to", market recaps, and anything negative.

For partnerships you cannot know from a headline alone whether the counterparty confirmed it;
set counterparty_confirmed to false unless the headline itself says both sides announced it.
Write reason_ko in Korean, one short sentence. Output only the requested JSON."""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["real", "noise"]},
                    "category": {"type": "string", "enum": [
                        "cex_listing", "usage_revenue", "mainnet_upgrade", "partnership",
                        "burn_buyback", "regulatory", "none"]},
                    "symbols": {"type": "array", "items": {"type": "string"}},
                    "counterparty_confirmed": {"type": "boolean"},
                    "reason_ko": {"type": "string"},
                },
                "required": ["id", "verdict", "category", "symbols", "counterparty_confirmed", "reason_ko"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

CREATE_NO_WINDOW = 0x08000000


def cli_args(model: str, prompt: str) -> list[str]:
    return ["-p", prompt, "--model", model, "--output-format", "json",
            "--no-session-persistence", "--disable-slash-commands", "--tools", "",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--system-prompt", SYSTEM, "--json-schema", json.dumps(SCHEMA)]


def cli_env() -> dict[str, str]:
    """구독으로만 돌게 — API 키가 섞이면 과금된다."""
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    return env


class NewsClassifier:
    def __init__(self, settings, store):
        self.s, self.store = settings, store
        self.exe = shutil.which("claude")
        self.model = settings.get("stage6.model", "haiku")
        self.batch = int(settings.get("stage6.batch_size", 20))
        self.timeout = float(settings.get("stage6.timeout_sec", 240))

    @property
    def enabled(self) -> bool:
        return bool(self.s.get("stage6.enabled", True)) and self.exe is not None

    def budget_left(self) -> tuple[bool, str]:
        calls = self.store.llm_calls(dt.date.today())
        if calls >= int(self.s.get("stage6.daily_call_limit", 50)):
            return False, f"오늘 호출 상한 {calls}회 도달"
        return True, ""

    async def _run(self, prompt: str) -> dict | None:
        kw = {}
        if sys.platform == "win32":
            kw["creationflags"] = CREATE_NO_WINDOW
        proc = await asyncio.create_subprocess_exec(
            self.exe, *cli_args(self.model, prompt), stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=cli_env(), cwd=tempfile.gettempdir(), **kw)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            log.warning("claude -p 시간 초과")
            return None
        try:
            return json.loads(out.decode("utf-8", "replace"))
        except ValueError:
            log.warning("claude -p 출력이 JSON 이 아님: %s", err.decode("utf-8", "replace")[:200])
            return None

    async def classify(self, items: list[dict]) -> dict[str, dict]:
        """{item id: 분류}. 상한·실패면 그 묶음은 비운다(추정하지 않는다)."""
        if not self.enabled or not items:
            return {}
        out: dict[str, dict] = {}
        for i in range(0, len(items), self.batch):
            ok, why = self.budget_left()
            if not ok:
                log.warning("뉴스 분류 중단 — %s", why)
                break
            chunk = items[i:i + self.batch]
            prompt = "Classify each headline (id<TAB>headline):\n" + "\n".join(
                f"{it['id']}\t{it['title']}" for it in chunk)
            d = await self._run(prompt)
            if d is None:
                continue
            u = d.get("usage") or {}
            # 구독이라 실제 청구는 없다. 환산값은 사용량 추적용으로만 적는다.
            self.store.log_llm(f"cli:{self.model}", int(u.get("input_tokens") or 0),
                               int(u.get("output_tokens") or 0), float(d.get("total_cost_usd") or 0))
            if d.get("is_error") or d.get("subtype") != "success":
                log.warning("claude -p 실패: %s", d.get("subtype"))
                continue
            for row in (d.get("structured_output") or {}).get("items", []):
                out[row["id"]] = row
        return out
