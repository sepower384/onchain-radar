"""Stage 6 뉴스 분류기 — 헤드라인을 '실질 호재 / 노이즈'로 가른다. ANTHROPIC_API_KEY 있을 때만.

규칙으로 가를 수 있는 거래소 공식 공지는 여기 오지 않는다(scanners/news_rules.py).
여기는 CryptoPanic 같은 **일반 뉴스**만 — 사람 말이 섞여 규칙으로는 못 가르는 것들.

비용 설계
- 헤드라인을 **batch_size 개씩 묶어 한 번에** 분류한다(20개면 호출 수 1/20).
- 하루 호출 상한(daily_call_limit)과 월 비용 상한(monthly_budget_usd)을 DB 에 기록하며 지킨다.
  상한에 닿으면 그날/그달은 분류를 멈추고 카드에 "분류 보류"로 둔다 — 조용히 돈을 쓰지 않는다.
- 분류는 판단이 얕은 작업이라 effort=low.

API 사용 (claude-api 스킬 기준, 2026-09)
- 공식 SDK `anthropic`(1.x) AsyncAnthropic. 구조화 출력은 output_config.format(json_schema).
- claude-opus-5 는 서버측 fallback 을 기본으로 켠다(betas server-side-fallback-2026-07-01 + fallbacks="default").
- stop_reason == "refusal" 은 content 를 읽기 전에 확인한다.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

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
Write reason_ko in Korean, one short sentence."""

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


class NewsClassifier:
    def __init__(self, settings, store):
        self.s, self.store = settings, store
        self.client = None
        key = settings.key("ANTHROPIC_API_KEY")
        if key and settings.get("stage6.enabled", False):
            import anthropic
            self.client = anthropic.AsyncAnthropic(api_key=key)
        self.model = settings.get("stage6.model", "claude-opus-5")
        self.batch = int(settings.get("stage6.batch_size", 20))
        price = settings.get("stage6.price_per_mtok", {"input": 5.0, "output": 25.0}) or {}
        self.p_in, self.p_out = float(price.get("input", 5.0)), float(price.get("output", 25.0))

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def budget_left(self) -> tuple[bool, str]:
        today = dt.date.today()
        calls = self.store.llm_calls(today)
        spent = self.store.llm_cost_month(today.year, today.month)
        if calls >= int(self.s.get("stage6.daily_call_limit", 200)):
            return False, f"오늘 호출 상한 {calls}회 도달"
        if spent >= float(self.s.get("stage6.monthly_budget_usd", 20)):
            return False, f"이달 비용 상한 ${spent:.2f} 도달"
        return True, ""

    async def classify(self, items: list[dict]) -> dict[str, dict]:
        """{item id: 분류}. 상한·거절·오류면 그 묶음은 비운다(추정하지 않는다)."""
        if not self.enabled or not items:
            return {}
        out: dict[str, dict] = {}
        for i in range(0, len(items), self.batch):
            ok, why = self.budget_left()
            if not ok:
                log.warning("뉴스 분류 중단 — %s", why)
                break
            chunk = items[i:i + self.batch]
            lines = "\n".join(f"{it['id']}\t{it['title']}" for it in chunk)
            try:
                resp = await self.client.beta.messages.create(
                    model=self.model,
                    max_tokens=4000,
                    system=SYSTEM,
                    messages=[{"role": "user", "content":
                               "Classify each headline (id<TAB>headline):\n" + lines}],
                    output_config={"effort": self.s.get("stage6.effort", "low"),
                                   "format": {"type": "json_schema", "schema": SCHEMA}},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            except Exception as e:  # noqa: BLE001 — 분류 실패가 스캔을 멈추면 안 된다
                log.warning("뉴스 분류 호출 실패: %s", type(e).__name__)
                continue
            self._record(resp)
            if resp.stop_reason == "refusal":
                log.warning("뉴스 분류 거절(refusal) — 이 묶음은 건너뛴다")
                continue
            text = next((b.text for b in resp.content if b.type == "text"), "")
            try:
                for row in json.loads(text).get("items", []):
                    out[row["id"]] = row
            except (ValueError, KeyError):
                log.warning("분류 응답 파싱 실패 (stop_reason=%s)", resp.stop_reason)
        return out

    def _record(self, resp) -> None:
        u = resp.usage
        cost = (u.input_tokens * self.p_in + u.output_tokens * self.p_out) / 1e6
        self.store.log_llm(self.model, u.input_tokens, u.output_tokens, cost)
