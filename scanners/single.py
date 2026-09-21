"""단일 코인 리포트 — /coin · /buy.

입력은 티커 또는 컨트랙트 주소.
  - 주소면 DexScreener 로 체인을 찾는다(주소는 체인마다 유일). 여러 체인에 같은 주소가 있으면 유동성 큰 쪽.
  - 티커면 코인게코 검색 → **동명 코인이 여럿이면 고르게 한다**(사양서: 인라인 버튼 선택).
    티커 하나로 임의 선택하지 않는다 — 가짜·동명 토큰 오매수의 입구가 여기다.
"""
from __future__ import annotations

import re

from bot.buy_links import SOL_ADDR, EVM_ADDR
from providers.dexscreener import pair_summary

from . import security, stage2_reaction, stage3_derivatives, stage5_revenue
from .model import Candidate


def looks_like_address(q: str) -> bool:
    return bool(EVM_ADDR.match(q) or SOL_ADDR.match(q))


async def resolve(q: str, P) -> dict:
    """{"kind": "address"|"choices"|"cg"|"none", ...}"""
    q = q.strip()
    if looks_like_address(q):
        pairs = await P.dexscreener.search(q) or []
        mine = [p for p in pairs if (p.get("baseToken") or {}).get("address", "").lower() == q.lower()]
        if not mine:
            return {"kind": "none", "why": "그 주소로 거래되는 DEX 페어를 찾지 못했습니다"}
        best = max(mine, key=lambda p: ((p.get("liquidity") or {}).get("usd")) or 0)
        return {"kind": "address", "pair": best}

    coins = await P.coingecko.search(q) or []
    exact = [c for c in coins if (c.get("symbol") or "").upper() == q.upper()]
    exact.sort(key=lambda c: c.get("market_cap_rank") or 10 ** 9)
    if not exact:
        return {"kind": "none", "why": f"'{q}' 와 심볼이 정확히 같은 코인이 없습니다"}
    if len(exact) == 1:
        return {"kind": "cg", "id": exact[0]["id"]}
    return {"kind": "choices", "items": [{"id": c["id"], "name": c.get("name"), "symbol": c.get("symbol"),
                                          "rank": c.get("market_cap_rank")} for c in exact[:8]]}


async def build_cg(cg_id: str, settings, P, store) -> Candidate | None:
    """코인게코 코인 하나로 메인 트랙 카드 전체를 만든다(Stage 2·3·5·안전)."""
    cfg = settings.get
    rows = await P.coingecko.markets_by_ids([cg_id])
    if not rows:
        return None
    r = rows[0]
    sym = (r.get("symbol") or "").upper()
    c = Candidate(coin_key=f"cg:{cg_id}", symbol=sym, name=r.get("name", ""), cg_id=cg_id)
    c.price, c.mcap, c.fdv = r.get("current_price"), r.get("market_cap"), r.get("fully_diluted_valuation")
    c.change_24h = r.get("price_change_percentage_24h_in_currency", r.get("price_change_percentage_24h"))
    c.change_7d = r.get("price_change_percentage_7d_in_currency")
    c.volume_24h = r.get("total_volume")

    bg = await P.bitget.markets() or {}
    m = bg.get(sym)
    if m:
        c.bitget_spot, c.bitget_futures = m.spot_symbol, m.futures_symbol
        c.funding_pct, c.oi_usd, c.bitget_volume_24h = m.funding_rate_pct, m.oi_usd, m.quote_volume_24h

    await stage2_reaction.enrich([c], P.bitget, store, cfg)
    stage2_reaction.evaluate(c, cfg)
    await stage3_derivatives.run([c], P.bitget, store, cfg, deep_top=1)
    await stage5_revenue.run([c], P.defillama, cfg)
    await security.run([c], P.goplus, P.coingecko, P.dexscreener, cfg, top=1)
    from scoring.engine import score
    score(c, cfg("scoring.weights", {}))
    return c


async def build_address(pair: dict, settings, P, store) -> Candidate:
    """DEX 전용 토큰 카드 — 마이크로캡과 같은 안전 규칙을 쓴다."""
    ps = pair_summary(pair)
    bg = await P.bitget.markets() or {}
    sym = (ps["symbol"] or "?").upper()
    micro = sym not in bg and (ps["fdv"] or ps["mcap"] or 0) < settings.get("stage7.max_fdv_usd", 2e7)
    c = Candidate(coin_key=f"{ps['chain']}:{ps['address']}", symbol=sym, name=ps["name"] or "",
                  track="micro" if micro else "main", chain=ps["chain"], address=ps["address"],
                  pair_url=ps["url"])
    c.price, c.fdv, c.mcap = ps["price"], ps["fdv"], ps["mcap"]
    c.liquidity_usd, c.volume_24h, c.change_24h = ps["liquidity_usd"], ps["volume_24h"], ps["change_24h"]
    m = bg.get(sym)
    if m and not micro:
        c.bitget_spot, c.bitget_futures = m.spot_symbol, m.futures_symbol
    c.security = await P.goplus.security(c.chain, c.address)
    s, line = security.score_security(c.security)
    st = c.stage("security")
    st.score, st.line = s, line
    r = c.stage("reaction")
    r.line = (f"24h 거래량 ${ps['volume_24h'] or 0:,.0f} · 유동성 ${ps['liquidity_usd'] or 0:,.0f} · "
              f"가격 {ps['change_24h'] or 0:+.1f}%")
    return c


def parse_query(text: str) -> str:
    parts = re.split(r"\s+", (text or "").strip(), maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""
