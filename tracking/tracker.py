"""성과 추적 — 보낸 신호가 1·3·7·14일 뒤 어떻게 됐는가. 반드시 구현(사양서).

규칙
- 가격 조회 실패·유동성 소멸·거래 중단 = **누락이 아니라 −100%** 로 기록한다(dead=1).
  누락시키면 망한 신호가 통계에서 사라져 적중률이 부풀려진다.
- 같은 기간 BTC 수익률과 비교한 **초과수익**을 함께 계산한다.
- 기록 시각은 실제로 잰 시각(ts). 추적은 1시간마다 도므로 목표 시점과 최대 1시간 차이.
"""
from __future__ import annotations

import logging
import statistics
import time
from collections import defaultdict

from sqlalchemy import select

from storage.db import Signal, SignalReturn, Store

log = logging.getLogger("tracker")


async def current_prices(signals: list[Signal], P) -> dict[str, float | None]:
    """coin_key → 지금 가격. 못 구하면 None(= 죽은 것으로 처리)."""
    out: dict[str, float | None] = {}
    cg_ids = sorted({s.coin_key[3:] for s in signals if s.coin_key.startswith("cg:")})
    if cg_ids:
        rows = await P.coingecko.markets_by_ids(cg_ids) or []
        by = {r["id"]: r.get("current_price") for r in rows}
        for i in cg_ids:
            out[f"cg:{i}"] = by.get(i)
    bg = None
    for s in signals:
        k = s.coin_key
        if k in out:
            continue
        if k.startswith("bitget:"):
            if bg is None:
                bg = await P.bitget.markets() or {}
            m = bg.get(k.split(":", 1)[1])
            out[k] = m.price if m else None
        elif ":" in k:                                      # chain:address
            chain, addr = k.split(":", 1)
            bp = await P.dexscreener.best_pair(chain, addr)
            liq = ((bp or {}).get("liquidity") or {}).get("usd") or 0
            try:
                px = float((bp or {}).get("priceUsd") or 0)
            except (TypeError, ValueError):
                px = 0.0
            # 유동성이 사실상 사라졌으면 가격이 찍혀 있어도 팔 수 없다 → 죽은 것
            out[k] = px if bp and px > 0 and liq >= 1000 else None
    return out


async def btc_now(P) -> float | None:
    rows = await P.coingecko.markets_by_ids(["bitcoin"]) or []
    if rows:
        return rows[0].get("current_price")
    bg = await P.bitget.markets() or {}
    return bg["BTC"].price if "BTC" in bg else None


async def run_once(settings, store: Store | None = None, P=None, now: float | None = None) -> int:
    from scanners.pipeline import make_providers
    store = store or Store(settings.get("storage.url"))
    own = P is None
    P = P or make_providers(settings)
    now = time.time() if now is None else now
    horizons = [int(h) for h in settings.get("tracking.horizons_days", [1, 3, 7, 14])]
    dead_ret = float(settings.get("tracking.dead_return_pct", -100))
    written = 0
    try:
        with store.session() as s:
            sigs = list(s.scalars(select(Signal).where(Signal.ts <= now - min(horizons) * 86400)))
            done = {(r.signal_id, r.horizon_days) for r in s.scalars(select(SignalReturn))}
        due = [(g, h) for g in sigs for h in horizons
               if (g.id, h) not in done and now >= g.ts + h * 86400]
        if not due:
            return 0
        prices = await current_prices(list({g.id: g for g, _ in due}.values()), P)
        btc = await btc_now(P)
        with store.session() as s:
            for g, h in due:
                px = prices.get(g.coin_key)
                dead = px is None or not g.price
                ret = dead_ret if dead else (px / g.price - 1) * 100
                btc_ret = (btc / g.btc_price - 1) * 100 if btc and g.btc_price else None
                s.add(SignalReturn(signal_id=g.id, horizon_days=h, ts=now, price=px, ret_pct=ret,
                                   btc_ret_pct=btc_ret,
                                   excess_pct=(ret - btc_ret) if btc_ret is not None else None,
                                   dead=int(dead)))
                written += 1
            s.commit()
        log.info("성과 기록 %d건", written)
        return written
    finally:
        if own:
            await P.close()


def stats(store: Store, track: str | None = None) -> dict:
    """/stats — 기간별 적중률·평균·BTC 대비 초과·최악, 그리고 Stage 별 기여도."""
    with store.session() as s:
        sigs = {g.id: g for g in s.scalars(select(Signal))}
        rets = list(s.scalars(select(SignalReturn)))
    by_h: dict[int, list[SignalReturn]] = defaultdict(list)
    for r in rets:
        g = sigs.get(r.signal_id)
        if g and (track is None or g.track == track):
            by_h[r.horizon_days].append(r)

    out = {"signals": sum(1 for g in sigs.values() if track is None or g.track == track), "horizons": {}}
    for h, rs in sorted(by_h.items()):
        vals = [r.ret_pct for r in rs]
        exc = [r.excess_pct for r in rs if r.excess_pct is not None]
        out["horizons"][h] = {
            "n": len(rs),
            "hit_rate": sum(v > 0 for v in vals) / len(vals) * 100,
            "avg": statistics.mean(vals),
            "median": statistics.median(vals),
            "avg_excess": statistics.mean(exc) if exc else None,
            "beat_btc": (sum(e > 0 for e in exc) / len(exc) * 100) if exc else None,
            "worst": min(vals),
            "dead": sum(r.dead for r in rs),
        }

    # Stage 기여도 — 7일(없으면 가장 긴 기간) 수익률을, 각 Stage 점수 상위 절반 vs 하위 절반으로 가른다.
    pick = 7 if 7 in by_h else (max(by_h) if by_h else None)
    contrib = {}
    if pick:
        pairs = [(sigs[r.signal_id].stages, r.ret_pct) for r in by_h[pick] if r.signal_id in sigs]
        names = {k for st, _ in pairs for k in st}
        for n in sorted(names):
            xs = [(st[n], ret) for st, ret in pairs if st.get(n) is not None]
            if len(xs) < 6:
                continue
            xs.sort(key=lambda x: x[0])
            half = len(xs) // 2
            lo = statistics.mean(r for _, r in xs[:half])
            hi = statistics.mean(r for _, r in xs[half:])
            contrib[n] = {"n": len(xs), "top_half": hi, "bottom_half": lo, "edge": hi - lo}
    out["contribution"] = {"horizon": pick, "stages": contrib}
    return out


def stats_text(st: dict, title: str = "신호 성과") -> str:
    from html import escape
    L = [f"📊 <b>{escape(title)}</b> — 누적 신호 {st['signals']}건"]
    if not st["horizons"]:
        L.append("아직 기간이 지난 신호가 없습니다. 첫 기록은 신호 발송 하루 뒤부터 쌓입니다.")
        return "\n".join(L)
    for h, v in st["horizons"].items():
        exc = f" · BTC 대비 {v['avg_excess']:+.1f}%p (이긴 비율 {v['beat_btc']:.0f}%)" \
            if v["avg_excess"] is not None else ""
        L.append(f"<b>{h}일</b> n={v['n']} · 적중 {v['hit_rate']:.0f}% · 평균 {v['avg']:+.1f}% · "
                 f"중앙 {v['median']:+.1f}%{exc} · 최악 {v['worst']:+.0f}%"
                 + (f" · 소멸 {v['dead']}건(−100% 처리)" if v["dead"] else ""))
    c = st["contribution"]
    if c["stages"]:
        L.append(f"\n<b>Stage 기여도</b> ({c['horizon']}일 수익률, 점수 상위 절반 − 하위 절반)")
        for n, v in sorted(c["stages"].items(), key=lambda kv: -kv[1]["edge"]):
            L.append(f"· {escape(n)}: {v['edge']:+.1f}%p (n={v['n']})")
    return "\n".join(L)
