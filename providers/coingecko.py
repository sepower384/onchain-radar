"""CoinGecko — 시총 상위 N, 코인 상세(체인별 컨트랙트 주소), CEX 상장 목록.

실측 요점 (probe/out/coingecko.json)
- 키 없이 호출하면 **연속 몇 번 만에 429** 가 난다(탐침 중에도 났다). Demo 키를 권장한다.
  Demo 키는 헤더 `x-cg-demo-api-key` 로 붙이고 호스트는 그대로 api.coingecko.com 이다.
- markets 는 배열. price_change_percentage_7d_in_currency 등은 요청 파라미터로 켜야 온다.
- coins/{id} 의 `platforms` = {체인이름: 컨트랙트} → 코인게코 코인을 DEX 링크로 잇는 다리.
  체인 이름이 DexScreener 와 다르다("binance-smart-chain" ↔ "bsc"). 아래 표로 맞춘다.
"""
from __future__ import annotations

from .base import Provider

# 코인게코 platform 이름 → 봇 표준 체인(= DexScreener chainId)
PLATFORM_TO_CHAIN = {
    "ethereum": "ethereum", "binance-smart-chain": "bsc", "base": "base",
    "arbitrum-one": "arbitrum", "solana": "solana", "polygon-pos": "polygon",
    "optimistic-ethereum": "optimism", "avalanche": "avalanche", "linea": "linea",
    "blast": "blast", "scroll": "scroll", "zksync": "zksync", "sui": "sui", "tron": "tron",
}


class CoinGecko(Provider):
    name = "coingecko"
    base_url = "https://api.coingecko.com/api/v3"

    def __init__(self, settings, client=None):
        super().__init__(settings, client)
        key = settings.key("COINGECKO_DEMO_KEY")
        if key:
            self.default_headers = {"x-cg-demo-api-key": key}
        else:
            # 키 없으면 공용 한도가 훨씬 낮다 — 스스로 속도를 줄인다.
            from .base import RateLimiter
            self.limiter = RateLimiter(10)

    def _ttl(self) -> float:
        return float(self.s.get("providers.cache_ttl_sec.coingecko_markets", 600))

    async def top_markets(self, n: int = 500) -> list[dict] | None:
        out: list[dict] = []
        pages = (n + 249) // 250
        for page in range(1, pages + 1):
            js = await self.get_json("/coins/markets", {
                "vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": page,
                "price_change_percentage": "1h,24h,7d,30d", "sparkline": "false",
            }, ttl=self._ttl())
            if not isinstance(js, list):
                return out or None
            out.extend(js)
        return out[:n]

    async def markets_by_ids(self, ids: list[str]) -> list[dict] | None:
        if not ids:
            return []
        out: list[dict] = []
        for i in range(0, len(ids), 250):
            js = await self.get_json("/coins/markets", {
                "vs_currency": "usd", "ids": ",".join(ids[i:i + 250]),
                "price_change_percentage": "1h,24h,7d,30d",
            }, ttl=self._ttl())
            if isinstance(js, list):
                out.extend(js)
        return out

    async def detail(self, coin_id: str, tickers: bool = False) -> dict | None:
        return await self.get_json(f"/coins/{coin_id}", {
            "localization": "false", "tickers": str(tickers).lower(), "market_data": "true",
            "community_data": "false", "developer_data": "false", "sparkline": "false",
        }, ttl=3600)

    async def platforms(self, coin_id: str) -> dict[str, str]:
        """{봇 표준 체인: 컨트랙트} — 모르는 체인은 버린다."""
        d = await self.detail(coin_id)
        out = {}
        for plat, addr in ((d or {}).get("platforms") or {}).items():
            ch = PLATFORM_TO_CHAIN.get(plat)
            if ch and addr:
                out[ch] = addr
        return out

    async def search(self, query: str) -> list[dict] | None:
        js = await self.get_json("/search", {"query": query}, ttl=600)
        return (js or {}).get("coins") if isinstance(js, dict) else None

    async def market_chart(self, coin_id: str, days: int = 15) -> dict | None:
        """{prices:[[ms,p]], total_volumes:[[ms,v]]} — 거래량 7일 평균 계산용."""
        return await self.get_json(f"/coins/{coin_id}/market_chart",
                                   {"vs_currency": "usd", "days": str(days), "interval": "daily"},
                                   ttl=1800)
