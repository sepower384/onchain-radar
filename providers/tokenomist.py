"""Tokenomist(구 Token Unlocks) — Stage 4 언락. TOKENOMIST_API_KEY 있을 때만.

DefiLlama 무료 언락(/emissions)은 HTTP 402 로 유료 전환됐다(2026-09-21 실측) — 이게 유일한 경로.

API 형식은 공식 OpenAPI(https://docs.tokenomist.ai/openapi.yaml, 2026-09-21 확인) 그대로다. 추측 없음.
  서버   https://api.tokenomist.ai     인증 헤더 x-api-key
  GET /v1/unlock/events/upcoming?minUnlockDate=…&maxUnlockDate=…&minValueToMarketCap=…
  → {status, metadata{queryDate, credit}, data:[{
        tokenId, tokenName, tokenSymbol, marketCap, releasedPercentage,
        upcomingEvent:{unlockDate, referencePrice,
          cliffUnlocks:{totalCliffAmount, totalCliffValue, valueToMarketCap,
            allocationBreakdown:[{allocationName, standardAllocationName, cliffAmount, cliffValue, unlockDate}]}}}]}
  valueToMarketCap = 언락 금액 ÷ 시총 × 100 = **유통량 대비 언락 비중(%)** (가격이 약분된다)
⚠ 키가 생기면 `python -m probe.probe_apis tokenomist` 로 실제 응답을 한 번 대조할 것.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .base import Provider, fnum


class Tokenomist(Provider):
    name = "tokenomist"
    base_url = "https://api.tokenomist.ai"

    def __init__(self, settings, client=None):
        super().__init__(settings, client)
        key = settings.key("TOKENOMIST_API_KEY")
        if key:
            self.default_headers = {"x-api-key": key}

    @property
    def enabled(self) -> bool:
        return self.s.has("TOKENOMIST_API_KEY")

    async def upcoming(self, days: int = 14, min_pct: float = 0.0) -> list[dict] | None:
        now = datetime.now(timezone.utc)
        params = {"minUnlockDate": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "maxUnlockDate": (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        if min_pct:
            params["minValueToMarketCap"] = str(min_pct)
        js = await self.get_json("/v1/unlock/events/upcoming", params, ttl=3600)
        if not js or not js.get("status"):
            return None
        return [parse(r) for r in js.get("data", []) if r.get("upcomingEvent")]


def parse(r: dict) -> dict:
    ev = r.get("upcomingEvent") or {}
    cl = ev.get("cliffUnlocks") or {}
    try:
        ts = datetime.fromisoformat(str(ev.get("unlockDate")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        ts = None
    recips: dict[str, float] = {}
    for a in cl.get("allocationBreakdown") or []:
        name = a.get("standardAllocationName") or a.get("allocationName") or "기타"
        recips[name] = recips.get(name, 0.0) + (fnum(a.get("cliffValue"), 0) or 0)
    return {
        "token_id": r.get("tokenId"), "symbol": (r.get("tokenSymbol") or "").upper(),
        "name": r.get("tokenName"), "mcap": fnum(r.get("marketCap")),
        "ts": ts, "amount": fnum(cl.get("totalCliffAmount")), "value_usd": fnum(cl.get("totalCliffValue")),
        "pct": fnum(cl.get("valueToMarketCap")),
        "recipients": sorted(recips.items(), key=lambda kv: -kv[1]),
    }


RECIP_KO = {"Private Investors": "VC", "Team": "팀", "Core Contributors": "팀", "Founders": "창업자",
            "Advisors": "자문", "Ecosystem": "생태계", "Community": "커뮤니티", "Foundation": "재단",
            "Treasury": "재단", "Public Investors": "공개판매", "Airdrop": "에어드랍"}


def recipients_ko(recips: list[tuple[str, float]], n: int = 2) -> str:
    return "·".join(RECIP_KO.get(k, k) for k, _ in recips[:n])
