"""DexScreener — DEX 페어·유동성·거래량·가격변화, 신규 프로필·부스트 토큰.

실측 요점 (probe/out/dexscreener.json)
- `token-pairs/v1/{chain}/{addr}` 은 **감싸는 객체 없이 배열**을 바로 준다.
- `latest/dex/search` 는 {"schemaVersion", "pairs": [...]}.
- 페어 필드: chainId, dexId, url, pairAddress, baseToken{address,name,symbol},
  priceUsd(문자열), volume{h24,h6,h1,m5}, priceChange{…}, liquidity{usd},
  fdv, marketCap, pairCreatedAt(ms), labels, info{socials,websites}.
- 체인 id 는 DexScreener 표기(solana·base·ethereum·bsc·arbitrum…)를 그대로 표준으로 쓴다.
"""
from __future__ import annotations

from .base import Provider, fnum


class DexScreener(Provider):
    name = "dexscreener"
    base_url = "https://api.dexscreener.com"

    def _ttl(self) -> float:
        return float(self.s.get("providers.cache_ttl_sec.dexscreener", 60))

    async def token_pairs(self, chain: str, address: str) -> list[dict] | None:
        js = await self.get_json(f"/token-pairs/v1/{chain}/{address}", ttl=self._ttl())
        return js if isinstance(js, list) else None

    async def best_pair(self, chain: str, address: str) -> dict | None:
        """유동성이 가장 큰 페어 — 구매 링크·차트 링크의 기준(사양서)."""
        pairs = await self.token_pairs(chain, address)
        if not pairs:
            return None
        # 이 토큰이 base 인 페어만. quote 로 들어간 페어를 고르면 엉뚱한 토큰 차트가 뜬다.
        mine = [p for p in pairs
                if (p.get("baseToken") or {}).get("address", "").lower() == address.lower()]
        pool = mine or pairs
        return max(pool, key=lambda p: fnum((p.get("liquidity") or {}).get("usd"), 0) or 0)

    async def tokens(self, chain: str, addresses: list[str]) -> list[dict] | None:
        """여러 토큰의 페어를 한 번에(최대 30개). `tokens/v1/{chain}/{a,b,c}`."""
        if not addresses:
            return []
        js = await self.get_json(f"/tokens/v1/{chain}/{','.join(addresses[:30])}", ttl=self._ttl())
        return js if isinstance(js, list) else None

    async def latest_profiles(self) -> list[dict] | None:
        js = await self.get_json("/token-profiles/latest/v1", ttl=self._ttl())
        return js if isinstance(js, list) else None

    async def latest_boosts(self) -> list[dict] | None:
        js = await self.get_json("/token-boosts/latest/v1", ttl=self._ttl())
        return js if isinstance(js, list) else None

    async def top_boosts(self) -> list[dict] | None:
        js = await self.get_json("/token-boosts/top/v1", ttl=self._ttl())
        return js if isinstance(js, list) else None

    async def search(self, q: str) -> list[dict] | None:
        js = await self.get_json("/latest/dex/search", {"q": q}, ttl=self._ttl())
        return (js or {}).get("pairs") if isinstance(js, dict) else None


def pair_summary(p: dict) -> dict:
    """페어 하나를 봇이 쓰는 모양으로 평탄화."""
    bt = p.get("baseToken") or {}
    vol = p.get("volume") or {}
    ch = p.get("priceChange") or {}
    txns = (p.get("txns") or {}).get("h24") or {}
    return {
        "chain": p.get("chainId"),
        "dex": p.get("dexId"),
        "pair_address": p.get("pairAddress"),
        "url": p.get("url"),
        "address": bt.get("address"),
        "symbol": bt.get("symbol"),
        "name": bt.get("name"),
        "price": fnum(p.get("priceUsd")),
        "liquidity_usd": fnum((p.get("liquidity") or {}).get("usd")),
        "volume_24h": fnum(vol.get("h24")),
        "volume_6h": fnum(vol.get("h6")),
        "volume_1h": fnum(vol.get("h1")),
        "change_1h": fnum(ch.get("h1")),
        "change_6h": fnum(ch.get("h6")),
        "change_24h": fnum(ch.get("h24")),
        "fdv": fnum(p.get("fdv")),
        "mcap": fnum(p.get("marketCap")),
        "created_ms": p.get("pairCreatedAt"),
        "buys_24h": txns.get("buys"),
        "sells_24h": txns.get("sells"),
        "labels": p.get("labels") or [],
    }
