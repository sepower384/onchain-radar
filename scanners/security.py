"""안전 신호 — GoPlus. 메인 트랙은 가점/감점, 마이크로캡은 하드 필터(stage7).

메인 트랙 코인은 코인게코 `platforms` 로 컨트랙트를 찾아야 GoPlus 를 부를 수 있다.
코인게코 상세 조회는 한도가 빡빡해서 **상위 후보에만** 한다(사양서: 무거운 조회는 좁힌 뒤).

컨트랙트를 고르는 순서: DEX 링크가 잘 나오는 체인을 먼저 — 솔라나·이더리움·베이스·BSC·아비트럼.
"""
from __future__ import annotations

import asyncio
import logging

from .model import Candidate

log = logging.getLogger("security")

CHAIN_PREF = ("solana", "ethereum", "base", "bsc", "arbitrum", "polygon", "optimism", "avalanche")


def score_security(sec) -> tuple[float | None, str]:
    if sec is None:
        return None, ""
    if sec.block_buy:
        return 0.0, f"🚨 {sec.block_reason()}"
    s = 100.0
    for flag, pts in (("honeypot", 100), ("cannot_sell", 100), ("owner_can_change", 30),
                      ("mintable", 15), ("blacklist", 20), ("proxy", 10)):
        if getattr(sec, flag, False):
            s -= pts
    tax = max(sec.buy_tax_pct or 0, sec.sell_tax_pct or 0)
    s -= min(40, tax * 3)
    if sec.open_source is False:
        s -= 20
    line = "특이 플래그 없음" if not sec.flags else " · ".join(sec.flags[:3])
    return max(0.0, s), line


def same_asset(c: Candidate, pair: dict | None, cfg) -> tuple[bool, str]:
    """이 DEX 토큰이 정말 그 코인인가. 아니면 CA·DEX 버튼·안전검사를 전부 뺀다.

    실측 반례(2026-09-21): 코인게코 BNB 의 platforms 에 **이더리움 위 옛 ERC-20 BNB**(0xb8c7…)가
    들어 있다. 유동성 $306K 라 유동성 하한만으로는 못 거른다. 그런데
      유동성/시총   옛 BNB 0.0003%  vs  진짜 UNI 0.32%   ← 1,000배 차이
      가격 괴리     옛 BNB +4.2%    vs  진짜 UNI +0.16%
    세 조건을 모두 통과해야 같은 자산으로 본다.
    """
    if not pair:
        return False, "DEX 페어 없음"
    liq = float((pair.get("liquidity") or {}).get("usd") or 0)
    if liq < cfg("buy_links.min_dex_liquidity_usd", 250000):
        return False, f"DEX 유동성 ${liq:,.0f} 부족"
    mc = c.mcap or c.fdv
    if mc and liq / mc * 100 < cfg("buy_links.min_dex_liq_to_mcap_pct", 0.05):
        return False, f"DEX 유동성이 시총의 {liq / mc * 100:.4f}% — 다른 토큰(브리지·구버전)으로 보입니다"
    try:
        dex_px = float(pair.get("priceUsd") or 0)
    except (TypeError, ValueError):
        dex_px = 0.0
    if c.price and dex_px:
        gap = abs(dex_px / c.price - 1) * 100
        if gap > cfg("buy_links.max_price_gap_pct", 3.0):
            return False, f"DEX 가격이 {gap:.1f}% 벌어져 있습니다"
    return True, ""


async def resolve_contract(c: Candidate, coingecko, dexscreener, cfg) -> None:
    """메인 트랙 코인에 (체인, 컨트랙트, 차트 페어)를 채운다. 같은 자산 검증을 못 넘으면 비운다."""
    if c.chain and c.address:
        return
    if not c.cg_id:
        return
    plats = await coingecko.platforms(c.cg_id)
    for ch in CHAIN_PREF:
        if ch not in plats:
            continue
        bp = await dexscreener.best_pair(ch, plats[ch])
        ok, why = same_asset(c, bp, cfg)
        if ok:
            c.chain, c.address = ch, plats[ch]
            c.pair_url = bp.get("url")
            c.liquidity_usd = (bp.get("liquidity") or {}).get("usd")
            return
        c.stage("security").detail.setdefault("rejected", []).append(f"{ch}: {why}")


async def run(cands: list[Candidate], goplus, coingecko, dexscreener, cfg, *, top: int = 25) -> None:
    sem = asyncio.Semaphore(4)

    async def one(c: Candidate):
        async with sem:
            try:
                await resolve_contract(c, coingecko, dexscreener, cfg)
                if c.chain and c.address:
                    c.security = await goplus.security(c.chain, c.address)
                s, line = score_security(c.security)
                st = c.stage("security")
                st.score, st.line = s, line
                if c.security is not None and c.security.block_buy:
                    c.risks.add("스캠")
            except Exception as e:  # noqa: BLE001
                log.warning("보안 %s 실패: %s", c.symbol, e)

    await asyncio.gather(*(one(c) for c in cands[:top]))
