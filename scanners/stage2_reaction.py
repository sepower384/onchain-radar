"""Stage 2. 가격·거래량 초기 반응 — "돈이 들어오기 시작했지만 아직 크게 안 쏜" 구간.

비용 절약 순서 (사양서: 무거운 조회는 좁힌 뒤에만)
  1) 티커 한 방으로 얻은 24h 등락으로 **예선**: 24h 가 최소 반응선 이상인 것만
  2) 예선 통과 코인만 비트겟 일봉 15개 → 7일 평균 거래량·7일 누적 등락 계산
  3) 비트겟에 없는 코인은 봇이 쌓은 스냅샷으로 7일 평균을 낸다. 없으면 "데이터 수집 중"

점수 = 0.6 × 거래량 점수 + 0.4 × 가격 위치 점수
  거래량: 7일 평균 대비 배수. 1배 → 0점, volume_ratio_full 배 → 100점
  가격  : 24h 가 [min, max] 안이면 100. 밑이면 비례, 위면 과열 쪽으로 감쇠
  늦음  : 24h > max 또는 7d > price_7d_max → "늦음" 태그 + late_penalty 감점
"""
from __future__ import annotations

import asyncio
import logging
import time

from .model import Candidate

log = logging.getLogger("stage2")


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def volume_score(ratio: float, full: float) -> float:
    if full <= 1:
        return 100.0 if ratio >= 1 else 0.0
    return _clamp((ratio - 1.0) / (full - 1.0) * 100)


def price_score(ch24: float, lo: float, hi: float) -> float:
    if ch24 < 0:
        return 0.0
    if ch24 < lo:
        return _clamp(ch24 / lo * 70)                 # 0 → 0점, lo 직전 → 70점
    if ch24 <= hi:
        return 100.0
    return _clamp(100 - (ch24 - hi) * 4)              # 상한을 1%p 넘을 때마다 4점


def evaluate(c: Candidate, cfg) -> None:
    """이미 volume_avg_7d · change_7d 가 채워진 후보에 점수를 매긴다(순수 함수)."""
    st = c.stage("reaction")
    lo, hi = cfg("stage2.price_24h_min_pct", 3.0), cfg("stage2.price_24h_max_pct", 15.0)
    max7 = cfg("stage2.price_7d_max_pct", 30.0)
    rmin, rfull = cfg("stage2.volume_ratio_min", 2.0), cfg("stage2.volume_ratio_full", 5.0)

    # 분자와 분모는 같은 출처여야 한다(비트겟 일봉 평균이면 분자도 비트겟 24h).
    today = c.bitget_volume_24h if c.volume_ratio_src == "bitget" else c.volume_24h
    if c.change_24h is None or today is None:
        st.score, st.line = None, "가격·거래량 데이터 없음"
        return
    if not c.volume_avg_7d:
        st.score, st.line = None, "거래량 7일 평균 데이터 수집 중입니다"
        return

    ratio = today / c.volume_avg_7d
    vs, ps = volume_score(ratio, rfull), price_score(c.change_24h, lo, hi)
    st.score = round(0.6 * vs + 0.4 * ps, 1)
    st.detail = {"volume_ratio": round(ratio, 2), "vol_score": round(vs), "price_score": round(ps)}

    late = c.change_24h > hi or (c.change_7d is not None and c.change_7d > max7)
    if late:
        c.tags.append("늦음")
        c.penalize("이미 급등(늦음)", cfg("stage2.late_penalty", 25), risk_tag=None)
        phase = "이미 많이 오른 뒤로 보입니다"
    elif ratio < rmin:
        phase = "거래량 반응은 아직 약합니다"
    elif c.change_24h < lo:
        phase = "가격은 아직 움직이기 전으로 보입니다"
    else:
        phase = "아직 초입으로 보입니다"
    ch7 = f", 7일 {c.change_7d:+.0f}%" if c.change_7d is not None else ""
    st.line = f"거래량 7일 평균 대비 {ratio:.1f}배, 가격 {c.change_24h:+.1f}%{ch7} ({phase})"


def prefilter(c: Candidate, cfg) -> bool:
    """예선 — 24h 가 최소 반응선 이상 + 최소 거래대금. 이미 급등한 것도 통과시킨다('늦음' 태그용)."""
    if c.change_24h is None or c.change_24h < cfg("stage2.price_24h_min_pct", 3.0):
        return False
    vol = c.bitget_volume_24h or c.volume_24h or 0
    return vol >= cfg("stage2.min_volume_24h_usd", 300000)


def prescore(c: Candidate, cfg) -> float:
    """정밀 조회(일봉) 대상 고르기용 싼 점수 — 가격 위치 + 거래대금 규모.
    상승장엔 예선 통과가 수백 개라 전부 일봉을 받으면 한 바퀴가 9분 걸렸다(첫 실행 555초)."""
    import math
    ps = price_score(c.change_24h or 0, cfg("stage2.price_24h_min_pct", 3.0),
                     cfg("stage2.price_24h_max_pct", 15.0))
    vol = c.bitget_volume_24h or c.volume_24h or 1
    return ps + min(40.0, math.log10(max(vol, 1)) * 5)


async def enrich(cands: list[Candidate], bitget, store, cfg, concurrency: int = 8) -> None:
    """예선 통과 후보에 7일 평균 거래량·7일 등락을 채운다."""
    sem = asyncio.Semaphore(concurrency)
    week_ago = time.time() - 8 * 86400

    async def one(c: Candidate):
        async with sem:
            # 현물이 있으면 현물 일봉, 선물만 있으면 선물 일봉. 분자(24h)도 같은 쪽이다
            # — BitgetMarket.quote_volume_24h 가 현물 없을 때 선물 거래대금으로 떨어지므로.
            sym, fut = (c.bitget_spot, False) if c.bitget_spot else (c.bitget_futures, True)
            if sym and c.bitget_volume_24h:
                rows = await bitget.daily_candles(sym, 15, futures=fut)
                if rows and len(rows) >= 4:
                    # 마지막 봉 = 오늘(진행 중). 그 앞 최대 7개로 평균을 낸다.
                    past = [r["quote_vol"] for r in rows[:-1][-7:] if r["quote_vol"]]
                    if past:
                        c.volume_avg_7d = sum(past) / len(past)
                        c.volume_ratio_src = "bitget"
                    if len(rows) >= 8 and rows[-8]["close"]:
                        c.change_7d = c.change_7d if c.change_7d is not None else \
                            (rows[-1]["close"] / rows[-8]["close"] - 1) * 100
                    return
            # 비트겟에 없는 코인 — 봇이 쌓은 스냅샷(추정 금지: 없으면 None 그대로)
            hist = store.price_history(c.coin_key, week_ago)
            vols = [h.volume_24h for h in hist if h.volume_24h]
            if len(vols) >= 12:          # 20분 주기로 최소 4시간치는 있어야 평균이라 부를 수 있다
                c.volume_avg_7d = sum(vols) / len(vols)
                c.volume_ratio_src = "snapshot"

    await asyncio.gather(*(one(c) for c in cands), return_exceptions=True)


async def run(cands: list[Candidate], bitget, store, cfg) -> list[Candidate]:
    passed = [c for c in cands if prefilter(c, cfg)]
    cap = int(cfg("stage2.max_deep", 150))
    if len(passed) > cap:
        passed.sort(key=lambda c: -prescore(c, cfg))
        passed = passed[:cap]
    log.info("Stage2 예선 %d / %d (정밀 조회 상한 %d)", len(passed), len(cands), cap)
    await enrich(passed, bitget, store, cfg)
    for c in passed:
        try:
            evaluate(c, cfg)
        except Exception as e:  # noqa: BLE001 — 한 코인 계산 실패가 전체를 멈추면 안 된다
            log.warning("Stage2 %s 실패: %s", c.symbol, e)
    return passed
