"""Stage 3. 파생 과열 필터 — 선물 상장 코인만.

재료 (비트겟, 전부 실측 확인)
  펀딩비  : 선물 tickers 의 fundingRate (8h). 유니버스 단계에서 이미 채워 둔다.
  OI     : 선물 tickers 의 holdingAmount × 가격. 24h 전 값은 **봇이 쌓은 스냅샷**으로만 비교한다.
  롱 비율 : account-long-short (종목별 호출이라 상위 후보에만)

점수 = 100 에서 과열 신호만큼 뺀다.
  펀딩 hot 이상 −25, extreme 이상 −50 / 음수 펀딩(숏이 돈을 냄)은 +5 (숏 스퀴즈 연료)
  롱 쏠림 −20
  OI 24h 급증인데 가격 정체 −25  ("레버리지만 쌓이는 중")
과열 판정이면 overheat_penalty 감점 + 리스크 태그 "과열".
선물 미상장·DEX 전용은 건너뛰고 "파생 데이터 없음"(score=None).
"""
from __future__ import annotations

import asyncio
import logging
import time

from .model import Candidate

log = logging.getLogger("stage3")


def evaluate(c: Candidate, cfg, *, oi_24h_ago: float | None = None,
             long_ratio: float | None = None) -> None:
    st = c.stage("derivatives")
    if not c.bitget_futures:
        st.score, st.line = None, "파생 데이터 없음 (선물 미상장)"
        return
    if c.funding_pct is None and c.oi_usd is None:
        st.score, st.line = None, "파생 데이터 조회 실패"
        return

    hot, extreme = cfg("stage3.funding_hot_pct", 0.05), cfg("stage3.funding_extreme_pct", 0.10)
    s, notes, overheat = 100.0, [], False

    f = c.funding_pct
    if f is not None:
        if f >= extreme:
            s -= 50
            overheat = True
            notes.append(f"펀딩 {f:.3f}% 과열")
        elif f >= hot:
            s -= 25
            notes.append(f"펀딩 {f:.3f}% 높음")
        elif f < 0:
            s += 5
            notes.append(f"펀딩 {f:.3f}% 음수(숏 우위)")
        else:
            notes.append("펀딩 중립")

    if long_ratio is not None:
        if long_ratio >= cfg("stage3.long_ratio_hot", 0.70):
            s -= 20
            overheat = True
            notes.append(f"롱 쏠림 {long_ratio * 100:.0f}%")
        else:
            notes.append(f"롱 {long_ratio * 100:.0f}%")

    oi_note = "OI 비교 데이터 수집 중"
    if oi_24h_ago and c.oi_usd:
        oi_ch = (c.oi_usd / oi_24h_ago - 1) * 100
        stalled = abs(c.change_24h or 0) < cfg("stage3.oi_price_stall_pct", 3.0)
        if oi_ch >= cfg("stage3.oi_jump_pct", 30.0) and stalled:
            s -= 25
            overheat = True
            oi_note = f"OI {oi_ch:+.0f}% 급증 대비 가격 정체"
        elif oi_ch > 0:
            oi_note = f"OI {oi_ch:+.0f}% 완만 증가" if oi_ch < 15 else f"OI {oi_ch:+.0f}% 증가"
        else:
            oi_note = f"OI {oi_ch:+.0f}%"
    notes.append(oi_note)

    st.score = round(max(0.0, min(100.0, s)), 1)
    st.detail = {"funding_pct": f, "long_ratio": long_ratio, "oi_usd": c.oi_usd}
    if overheat:
        c.penalize("파생 과열", cfg("stage3.overheat_penalty", 20), risk_tag="과열")
        st.line = " · ".join(notes) + " — 과열 경고입니다"
    else:
        st.line = " · ".join(notes) + " — 과열 아닙니다"


async def run(cands: list[Candidate], bitget, store, cfg, *, deep_top: int = 30) -> None:
    """모든 후보에 펀딩·OI 로 1차 평가. 롱숏 비율은 반응 점수 상위 deep_top 에만 조회."""
    day_ago = time.time() - 86400
    fut = [c for c in cands if c.bitget_futures]
    fut.sort(key=lambda c: -((c.stages.get("reaction") and c.stages["reaction"].score) or 0))
    deep = {c.coin_key for c in fut[:deep_top]}

    sem = asyncio.Semaphore(6)

    async def one(c: Candidate):
        try:
            snap = store.snapshot_near(c.coin_key, day_ago)
            lr = None
            if c.coin_key in deep:
                async with sem:
                    lr = await bitget.long_short(c.bitget_futures)
            evaluate(c, cfg, oi_24h_ago=snap.oi_usd if snap else None, long_ratio=lr)
        except Exception as e:  # noqa: BLE001
            log.warning("Stage3 %s 실패: %s", c.symbol, e)
            c.stage("derivatives").score = None

    await asyncio.gather(*(one(c) for c in cands))
