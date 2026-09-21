"""Nansen — 라벨된 스마트머니 순매수(Phase 3, 유료 옵션). NANSEN_API_KEY 있을 때만.

없으면 Stage 1 이 "상위 비거래소 지갑 잔고 변화"로 대체 계산한다(사양서). 있으면 그 위에 얹는다.

형식은 공식 문서(docs.nansen.ai/api/smart-money/netflows, 2026-09-21 확인) 그대로:
  POST https://api.nansen.ai/api/v1/smart-money/netflow     헤더 apiKey
  body {chains:[...], filters:{include_stablecoins:false,...}, order_by:[{field,direction}], pagination:{page,per_page}}
  행   token_address · token_symbol · chain · net_flow_1h/24h/7d/30d_usd · trader_count · market_cap_usd
⚠ 체인 이름이 다르다: BSC 를 "bnb" 로 부른다.
⚠ 키가 생기면 응답을 한 번 실제로 대조할 것(감싸는 키가 data 인지 확인 — 둘 다 받게 짰다).
"""
from __future__ import annotations

from .base import Provider, fnum

CHAIN = {"ethereum": "ethereum", "base": "base", "arbitrum": "arbitrum", "bsc": "bnb",
         "solana": "solana", "polygon": "polygon", "optimism": "optimism", "avalanche": "avalanche"}
BACK = {v: k for k, v in CHAIN.items()}


class Nansen(Provider):
    name = "nansen"
    base_url = "https://api.nansen.ai"

    def __init__(self, settings, client=None):
        super().__init__(settings, client)
        key = settings.key("NANSEN_API_KEY")
        if key:
            self.default_headers = {"apiKey": key}

    @property
    def enabled(self) -> bool:
        return self.s.has("NANSEN_API_KEY")

    async def netflows(self, chains: list[str], per_page: int = 200) -> dict[tuple[str, str], dict] | None:
        """(봇 체인, 소문자 주소) → {net_24h, net_7d, traders}. 7일 순유입 큰 순."""
        body = {"chains": [CHAIN[c] for c in chains if c in CHAIN],
                "filters": {"include_stablecoins": False},
                "order_by": [{"field": "net_flow_7d_usd", "direction": "DESC"}],
                "pagination": {"page": 1, "per_page": per_page}}
        js = await self.post_json("/api/v1/smart-money/netflow", body, ttl=1800)
        if js is None:
            return None
        rows = js.get("data", js) if isinstance(js, dict) else js
        out = {}
        for r in rows or []:
            ch = BACK.get(r.get("chain"), r.get("chain"))
            addr = r.get("token_address") or ""
            key = (ch, addr if ch == "solana" else addr.lower())
            out[key] = {"net_24h": fnum(r.get("net_flow_24h_usd")), "net_7d": fnum(r.get("net_flow_7d_usd")),
                        "traders": r.get("trader_count"), "symbol": r.get("token_symbol")}
        return out
