"""GeckoTerminal — 체인별 트렌딩·신규 풀. DexScreener 의 보조 소스.

실측 요점 (probe/out/geckoterminal.json)
- 헤더 `Accept: application/json;version=20230302` 필요.
- JSON:API 형식: {"data":[{"id":"base_0x…","type":"pool","attributes":{…},"relationships":{…}}]}
- 분당 30콜 공개 한도 — config 에서 25로 잡았다.
- 네트워크 id 가 DexScreener 와 다르다(eth ↔ ethereum). 아래 표로 맞춘다.
"""
from __future__ import annotations

from .base import Provider, fnum

NET = {"ethereum": "eth", "bsc": "bsc", "base": "base", "arbitrum": "arbitrum",
       "solana": "solana", "polygon": "polygon_pos", "avalanche": "avax", "optimism": "optimism"}


class GeckoTerminal(Provider):
    name = "geckoterminal"
    base_url = "https://api.geckoterminal.com/api/v2"
    default_headers = {"Accept": "application/json;version=20230302"}

    async def trending(self, chain: str) -> list[dict] | None:
        net = NET.get(chain)
        if not net:
            return None
        js = await self.get_json(f"/networks/{net}/trending_pools", {"page": 1}, ttl=120)
        return [pool_row(chain, d) for d in (js or {}).get("data", [])] if js else None

    async def new_pools(self, chain: str) -> list[dict] | None:
        net = NET.get(chain)
        if not net:
            return None
        js = await self.get_json(f"/networks/{net}/new_pools", {"page": 1}, ttl=120)
        return [pool_row(chain, d) for d in (js or {}).get("data", [])] if js else None


def pool_row(chain: str, d: dict) -> dict:
    a = d.get("attributes") or {}
    rel = d.get("relationships") or {}
    base_id = (((rel.get("base_token") or {}).get("data")) or {}).get("id", "")
    # base_token id 는 "base_0xabc…" 형식 — 체인 접두어를 떼어 주소만 남긴다.
    address = base_id.split("_", 1)[1] if "_" in base_id else None
    vol = a.get("volume_usd") or {}
    ch = a.get("price_change_percentage") or {}
    return {
        "chain": chain,
        "pool_address": a.get("address"),
        "address": address,
        "name": a.get("name"),
        "liquidity_usd": fnum(a.get("reserve_in_usd")),
        "volume_24h": fnum(vol.get("h24")),
        "change_24h": fnum(ch.get("h24")),
        "fdv": fnum(a.get("fdv_usd")),
        "mcap": fnum(a.get("market_cap_usd")),
        "created_at": a.get("pool_created_at"),
    }
