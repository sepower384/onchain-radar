"""스캔 유니버스 — 풀 A(비트겟 상장 전체) ∪ 풀 B(코인게코 시총 상위 N).

풀 C(DEX 트렌딩·신규)는 Stage 7 이 따로 만든다(scanners/stage7_microcap.py).

합치는 규칙
- 코인게코에 있는 코인은 `cg:<id>` 가 키다. 비트겟 base 심볼을 코인게코 심볼에 붙인다.
- 같은 심볼이 코인게코에 둘 이상이면 **시총 큰 쪽**만 비트겟과 잇는다(동명 코인 오결합 방지).
- 비트겟에만 있고 코인게코 상위에 없는 코인은 `bitget:<BASE>` 로 둔다.
  이 코인은 컨트랙트를 모르므로 DEX 링크·GoPlus·홀더 신호가 자연히 빠진다(재정규화).
- 스테이블코인은 뺀다.
"""
from __future__ import annotations

import logging

from .model import Candidate

log = logging.getLogger("universe")


async def build_main(settings, bitget, coingecko) -> tuple[list[Candidate], dict]:
    """(후보 목록, 부가정보). 부가정보에 btc 가격·비트겟 원자료를 담아 뒤 Stage 가 쓴다."""
    stables = {s.upper() for s in settings.get("universe.stable_symbols", [])}
    use_a = settings.get("universe.pool_a_bitget", True)
    top_n = int(settings.get("universe.pool_b_coingecko_top", 500) or 0)

    bg = (await bitget.markets()) if use_a else None
    cg = (await coingecko.top_markets(top_n)) if top_n else None
    if bg is None and cg is None:
        log.error("비트겟·코인게코 둘 다 실패 — 유니버스를 만들 수 없다")
        return [], {}

    # 심볼 → 코인게코 행 (같은 심볼이면 시총 큰 쪽)
    cg_by_sym: dict[str, dict] = {}
    for row in cg or []:
        sym = (row.get("symbol") or "").upper()
        if not sym:
            continue
        cur = cg_by_sym.get(sym)
        if cur is None or (row.get("market_cap") or 0) > (cur.get("market_cap") or 0):
            cg_by_sym[sym] = row

    out: dict[str, Candidate] = {}

    for sym, row in cg_by_sym.items():
        if settings.get("universe.exclude_stable", True) and sym in stables:
            continue
        c = Candidate(coin_key=f"cg:{row['id']}", symbol=sym, name=row.get("name", ""),
                      cg_id=row["id"])
        c.price = row.get("current_price")
        c.mcap = row.get("market_cap")
        c.fdv = row.get("fully_diluted_valuation")
        c.change_24h = row.get("price_change_percentage_24h_in_currency",
                               row.get("price_change_percentage_24h"))
        c.change_7d = row.get("price_change_percentage_7d_in_currency")
        c.volume_24h = row.get("total_volume")
        out[c.coin_key] = c

    by_sym = {c.symbol: c for c in out.values()}
    for base, m in (bg or {}).items():
        if settings.get("universe.exclude_stable", True) and base in stables:
            continue
        c = by_sym.get(base)
        if c is None:
            c = Candidate(coin_key=f"bitget:{base}", symbol=base, name=base)
            c.price, c.change_24h, c.volume_24h = m.price, m.change24h_pct, m.quote_volume_24h
            out[c.coin_key] = c
            by_sym[base] = c
        c.bitget_spot = m.spot_symbol
        c.bitget_futures = m.futures_symbol
        c.funding_pct = m.funding_rate_pct
        c.oi_usd = m.oi_usd
        c.bitget_volume_24h = m.quote_volume_24h

    btc = next((r for r in cg or [] if r.get("id") == "bitcoin"), None)
    btc_price = (btc or {}).get("current_price")
    if btc_price is None and bg and "BTC" in bg:
        btc_price = bg["BTC"].price
    extra = {
        "btc_price": btc_price,
        "bitget": bg or {},
        "counts": {"bitget": len(bg or {}), "coingecko": len(cg or []), "merged": len(out)},
    }
    log.info("유니버스: 비트겟 %d · 코인게코 %d → 합쳐서 %d",
             len(bg or {}), len(cg or []), len(out))
    return list(out.values()), extra
