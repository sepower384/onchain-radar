"""Stage 7. 마이크로캡·DEX 전용 트랙 (/micro). 모든 카드에 "초고위험" 고정.

후보 수집 (풀 C, 전부 무료·키 없음)
  DexScreener  최신 프로필 · 최신 부스트 · 상위 부스트
  GeckoTerminal 체인별 트렌딩 풀 · 신규 풀
  → (체인, 컨트랙트) 로 중복 제거 → DexScreener tokens 일괄 조회로 가장 유동성 큰 페어를 붙인다.

필수 안전 필터 (하나라도 걸리면 **제외**)
  GoPlus 허니팟 · 매도 불가 · 고세금(max_tax_pct 초과)
  유동성 < min_liquidity_usd
  페어 생성 후 min_pair_age_hours 미만
  FDV > max_fdv_usd
경고 표시 (🚨, 제외는 안 함 — 감점)
  추가 발행 가능 · 오너 권한 · 블랙리스트/동결 권한
  상위10 개인 지갑 집중도 > max_top10_holder_pct
  24h 거래량 / 유동성 > wash_volume_liquidity_ratio → 워시트레이딩 의심

정렬 = 사양서 "시총 오름차순 + 안전점수 + 매집 점수".
  시총은 로그 스케일로 점수화해 합산한다(작을수록 가점). 순수 오름차순이면 쓰레기가 맨 위로 온다.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import defaultdict

from providers.dexscreener import pair_summary

from .model import Candidate
from .security import score_security
from .stage1_accumulation import evaluate as eval_accum
from .stage1_accumulation import snapshot as snap_accum

log = logging.getLogger("stage7")

CEX_DEX_IDS = set()   # 자리만 — CEX 상장 여부는 비트겟 원자료로 판정한다


async def collect_pool_c(settings, dexscreener, geckoterminal) -> dict[tuple[str, str], dict]:
    """(chain, address) → 출처 메모."""
    chains = set(settings.get("universe.pool_c_chains", ["solana", "base", "ethereum", "bsc"]))
    found: dict[tuple[str, str], dict] = {}

    def add(chain, addr, src):
        if chain in chains and addr:
            found.setdefault((chain, addr), {"src": set()})["src"].add(src)

    for fn, src in ((dexscreener.latest_profiles, "ds_profile"),
                    (dexscreener.latest_boosts, "ds_boost"),
                    (dexscreener.top_boosts, "ds_topboost")):
        for row in await fn() or []:
            add(row.get("chainId"), row.get("tokenAddress"), src)

    for ch in chains:
        for fn, src in ((geckoterminal.trending, "gt_trend"), (geckoterminal.new_pools, "gt_new")):
            for row in await fn(ch) or []:
                add(ch, row.get("address"), src)
    log.info("풀 C 후보 %d개 (%s)", len(found), ", ".join(sorted(chains)))
    return found


async def attach_pairs(found: dict, dexscreener) -> list[Candidate]:
    by_chain: dict[str, list[str]] = defaultdict(list)
    for (ch, addr) in found:
        by_chain[ch].append(addr)
    out: list[Candidate] = []
    for ch, addrs in by_chain.items():
        for i in range(0, len(addrs), 30):
            pairs = await dexscreener.tokens(ch, addrs[i:i + 30]) or []
            best: dict[str, dict] = {}
            for p in pairs:
                bt = (p.get("baseToken") or {}).get("address", "")
                key = bt.lower() if ch != "solana" else bt
                liq = ((p.get("liquidity") or {}).get("usd")) or 0
                if key and (key not in best or liq > ((best[key].get("liquidity") or {}).get("usd") or 0)):
                    best[key] = p
            for p in best.values():
                ps = pair_summary(p)
                c = Candidate(coin_key=f"{ch}:{ps['address']}", symbol=(ps["symbol"] or "?").upper(),
                              name=ps["name"] or "", track="micro", chain=ch, address=ps["address"],
                              pair_url=ps["url"])
                c.price, c.fdv, c.mcap = ps["price"], ps["fdv"], ps["mcap"]
                c.liquidity_usd, c.volume_24h = ps["liquidity_usd"], ps["volume_24h"]
                c.change_24h = ps["change_24h"]
                if ps["created_ms"]:
                    c.pair_age_h = (time.time() * 1000 - ps["created_ms"]) / 3.6e6
                c.stage("reaction").detail = ps
                out.append(c)
    return out


def hard_filter(c: Candidate, cfg) -> str | None:
    """제외 사유. None 이면 통과."""
    if (c.fdv or c.mcap or 0) > cfg("stage7.max_fdv_usd", 20_000_000):
        return "FDV 초과"
    if (c.fdv or c.mcap) is None:
        return "시총 정보 없음"
    if (c.liquidity_usd or 0) < cfg("stage7.min_liquidity_usd", 30000):
        return "유동성 부족"
    if c.pair_age_h is None or c.pair_age_h < cfg("stage7.min_pair_age_hours", 24):
        return "너무 새 페어"
    return None


def security_filter(c: Candidate, cfg) -> str | None:
    sec = c.security
    if sec is None:
        return "보안 검사 불가"            # 마이크로캡은 모르면 뺀다 — 안전 쪽으로
    if sec.block_buy:
        return sec.block_reason()
    tax = max(sec.buy_tax_pct or 0, sec.sell_tax_pct or 0)
    if tax > cfg("stage7.max_tax_pct", 10.0):
        return f"세금 {tax:.0f}%"
    return None


def reaction_score(c: Candidate) -> float | None:
    d = c.stage("reaction").detail or {}
    v24, v6, v1 = d.get("volume_24h"), d.get("volume_6h"), d.get("volume_1h")
    if not v24:
        return None
    # 최근 1h·6h 가 24h 평균 속도보다 빠르면 '들어오는 중'
    accel = 0.0
    if v6:
        accel += min(2.0, (v6 / 6) / (v24 / 24)) * 25
    if v1:
        accel += min(2.0, v1 / (v24 / 24)) * 25
    ch = d.get("change_24h") or 0
    price_part = 30 if 0 < ch <= 40 else (10 if ch > 40 else 0)
    buys, sells = d.get("buys_24h") or 0, d.get("sells_24h") or 0
    flow = 20 * (buys / (buys + sells)) if buys + sells else 0
    return round(min(100.0, accel + price_part + flow), 1)


def mcap_score(v: float | None) -> float:
    """$100K → 100점, $20M → 0점 (로그 스케일)."""
    if not v or v <= 0:
        return 0.0
    lo, hi = math.log10(1e5), math.log10(2e7)
    return max(0.0, min(100.0, (hi - math.log10(v)) / (hi - lo) * 100))


async def run(settings, dexscreener, geckoterminal, goplus, store, bitget_bases: set[str]) -> dict:
    cfg = settings.get
    found = await collect_pool_c(settings, dexscreener, geckoterminal)
    cands = await attach_pairs(found, dexscreener)

    stats = {"collected": len(found), "paired": len(cands), "dropped": defaultdict(int)}
    survivors: list[Candidate] = []
    for c in cands:
        if c.symbol in bitget_bases:          # CEX 상장된 건 메인 트랙 몫
            stats["dropped"]["CEX 상장"] += 1
            continue
        why = hard_filter(c, cfg)
        if why:
            stats["dropped"][why] += 1
            continue
        survivors.append(c)

    sem = asyncio.Semaphore(4)

    async def sec(c):
        async with sem:
            c.security = await goplus.security(c.chain, c.address)

    await asyncio.gather(*(sec(c) for c in survivors), return_exceptions=True)

    passed: list[Candidate] = []
    week_ago = time.time() - 7 * 86400
    for c in survivors:
        why = security_filter(c, cfg)
        if why:
            stats["dropped"][f"보안:{why}"] += 1
            continue
        s, line = score_security(c.security)
        st = c.stage("security")
        st.score, st.line = s, line
        sec_ = c.security
        if sec_.mintable or sec_.owner_can_change or sec_.blacklist:
            c.risks.add("권한위험")
        if sec_.top10_pct is not None and sec_.top10_pct > cfg("stage7.max_top10_holder_pct", 50.0):
            c.tags.append(f"🚨 상위10 {sec_.top10_pct:.0f}%")
            c.penalize("상위 지갑 집중", 15, risk_tag="집중")
        if c.volume_24h and c.liquidity_usd and \
                c.volume_24h / c.liquidity_usd > cfg("stage7.wash_volume_liquidity_ratio", 20.0):
            c.tags.append("🚨 워시트레이딩 의심")
            c.penalize("거래량/유동성 이상치", 20, risk_tag="워시")

        # 이미 쏜 코인 — 마이크로캡은 하루 +430% 짜리가 1위로 올라왔다(첫 실행).
        ch = c.change_24h or 0
        if ch > cfg("stage7.late_24h_pct", 100.0):
            c.tags.append("늦음")
            c.penalize(f"24h {ch:+.0f}% 이미 급등(늦음)", cfg("stage7.late_penalty", 30))
        r = c.stage("reaction")
        r.score = reaction_score(c)
        d = r.detail
        r.line = (f"24h 거래량 ${d.get('volume_24h') or 0:,.0f} · 유동성 ${c.liquidity_usd or 0:,.0f} · "
                  f"가격 {d.get('change_24h') or 0:+.1f}%")
        snap_accum(store, c)
        store.upsert_contract(c.coin_key, c.symbol, c.chain, c.address)
        eval_accum(c, store.holder_history(c.coin_key, week_ago),
                   tops=store.holder_top_history(c.coin_key, week_ago))
        c.stage("mcap").score = mcap_score(c.fdv or c.mcap)
        passed.append(c)

    stats["passed"] = len(passed)
    stats["dropped"] = dict(stats["dropped"])
    return {"candidates": passed, "stats": stats}


def micro_rank(cands: list[Candidate]) -> list[Candidate]:
    """시총(작을수록) 40% + 안전 35% + 매집 25%. 매집이 없으면 나머지로 재정규화."""
    for c in cands:
        parts = [(0.40, c.stages.get("mcap")), (0.35, c.stages.get("security")),
                 (0.25, c.stages.get("accumulation")), (0.25, c.stages.get("reaction"))]
        num = den = 0.0
        used = 0
        for w, st in parts:
            if st is not None and st.score is not None:
                num, den, used = num + w * st.score, den + w, used + 1
        pen = sum(p for _, p in c.penalties)
        c.score = round(max(0.0, (num / den if den else 0) - pen), 1)
        c.signals_used = used
    return sorted(cands, key=lambda c: -c.score)
