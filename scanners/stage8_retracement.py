"""Stage 8. 급락 후 피보나치 되돌림 — "잘 가다가 30~50% 빠진 코인이 지지선에서 버티는가".

강회장 요청(2026-09-21): "잘 가다가 급락 약 30~50% 하락 이런 것도 피보나치 되돌림 적용해서
패턴분석 후 알림". 메인 스캔(초입 포착)과는 반대편 — **눌림목**을 본다.

정의 (전부 config.stage8)
  잘 가다가  : 스윙 고점 H 이전 구간의 저점 L 에서 H 까지 +min_leg_gain 이상 올랐다
  급락      : 현재가가 H 대비 dd_min ~ dd_max 하락 (기본 30~50%)
  되돌림 비율: r = (H − 현재가) / (H − L)
  피보나치  : 0.382 · 0.5 · 0.618 · 0.786 되돌림 가격. 0.618~0.65 = 골든포켓
  무효화    : r > invalid_ratio(0.886) → 상승 구조가 깨졌다고 보고 제외

떨어지는 칼날을 잡지 않도록 **반등 확인**이 있어야 알림 대상이 된다
  ① 최근 N일 저점 대비 bounce_min_pct 이상 반등
  ② 마지막 봉이 양봉이거나 아래꼬리가 봉 길이의 절반 이상 (매수세 흔적)
  ③ 최근 3일 평균 거래량 < 하락 구간 평균 × exhaustion_ratio (매도 소진)
세 개 중 require_confirms 개 이상.

이건 패턴 분석이지 예측이 아니다 — 카드에 무효화 가격을 반드시 같이 찍는다.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from .model import Candidate

log = logging.getLogger("stage8")

FIBS = (0.382, 0.5, 0.618, 0.786)
LEVEL_SCORE = {0.382: 20, 0.5: 32, 0.618: 40, 0.786: 25}
LEVEL_NAME = {0.382: "0.382", 0.5: "0.5", 0.618: "0.618(골든포켓)", 0.786: "0.786(마지막 방어선)"}


@dataclass
class Retrace:
    high: float
    high_ts: float
    low: float
    low_ts: float
    price: float
    leg_gain_pct: float
    drawdown_pct: float
    ratio: float                      # 되돌림 비율 r
    level: float | None               # 가장 가까운 피보나치
    level_gap: float                  # |r − level|
    levels: dict[float, float]        # 피보나치 → 가격
    invalid_price: float
    bounce_pct: float
    bullish_candle: bool
    long_lower_wick: bool
    volume_exhausted: bool | None
    confirms: int
    score: float
    notes: list[str] = field(default_factory=list)


def analyze(rows: list[dict], cfg) -> Retrace | None:
    """일봉(오래된→최근) → 되돌림 판정. 조건 밖이면 None. 순수 함수 — 합성 캔들로 테스트한다."""
    win = int(cfg("stage8.swing_window_days", 60))
    leg_back = int(cfg("stage8.leg_lookback_days", 45))
    min_after = int(cfg("stage8.min_days_since_high", 2))
    if len(rows) < 20:
        return None
    rows = rows[-(win + leg_back):]
    recent = rows[-win:]
    base = len(rows) - len(recent)

    # 1) 스윙 고점 — 최근 win 일 안. 너무 최근(오늘·어제)이면 아직 눌림이 아니다.
    iH_local = max(range(len(recent)), key=lambda i: recent[i]["high"] or 0)
    iH = base + iH_local
    if len(rows) - 1 - iH < min_after:
        return None
    H = rows[iH]["high"]

    # 2) 그 고점 이전의 저점 — 상승 구간의 시작
    start = max(0, iH - leg_back)
    if iH - start < 3:
        return None
    iL = min(range(start, iH), key=lambda i: rows[i]["low"] or float("inf"))
    L = rows[iL]["low"]
    if not H or not L or H <= L:
        return None
    leg = H / L - 1
    if leg * 100 < cfg("stage8.min_leg_gain_pct", 60.0):
        return None

    # 3) 급락 폭
    P = rows[-1]["close"]
    dd = (1 - P / H) * 100
    if not (cfg("stage8.dd_min_pct", 30.0) <= dd <= cfg("stage8.dd_max_pct", 50.0)):
        return None

    # 4) 되돌림 비율과 피보나치
    r = (H - P) / (H - L)
    if r > cfg("stage8.invalid_ratio", 0.886):
        return None
    levels = {f: H - f * (H - L) for f in FIBS}
    tol = cfg("stage8.level_tolerance", 0.06)
    near = min(FIBS, key=lambda f: abs(r - f))
    gap = abs(r - near)
    level = near if gap <= tol else None

    # 5) 반등 확인
    n = int(cfg("stage8.bounce_lookback_days", 5))
    low_recent = min(x["low"] for x in rows[-n:])
    bounce = (P / low_recent - 1) * 100 if low_recent else 0.0
    last = rows[-1]
    rng = (last["high"] - last["low"]) or 1e-12
    bullish = last["close"] > last["open"]
    lower_wick = (min(last["open"], last["close"]) - last["low"]) / rng >= 0.5
    drop = rows[iH + 1:-3] or rows[iH:-3]
    vol_drop = [x["quote_vol"] for x in drop if x.get("quote_vol")]
    vol_recent = [x["quote_vol"] for x in rows[-3:] if x.get("quote_vol")]
    exhausted = None
    if vol_drop and vol_recent:
        exhausted = (sum(vol_recent) / len(vol_recent)) < (sum(vol_drop) / len(vol_drop)) * \
            cfg("stage8.exhaustion_ratio", 0.8)

    confirms = sum([bounce >= cfg("stage8.bounce_min_pct", 3.0), bullish or lower_wick, bool(exhausted)])

    # 6) 점수 — 레벨 품질 + 반등 + 매도 소진 + 이전 상승 강도
    s = float(LEVEL_SCORE.get(level, 0)) if level else max(0.0, 15 - gap * 100)
    s += min(30.0, bounce * 3)
    s += 15 if exhausted else 0
    s += min(15.0, leg * 10)
    notes = []
    if level == 0.618 or (0.60 <= r <= 0.66):
        notes.append("골든포켓")
    if exhausted:
        notes.append("매도 소진")
    if bullish:
        notes.append("양봉")
    if lower_wick:
        notes.append("아래꼬리")

    return Retrace(high=H, high_ts=rows[iH]["ts"], low=L, low_ts=rows[iL]["ts"], price=P,
                   leg_gain_pct=leg * 100, drawdown_pct=dd, ratio=r, level=level, level_gap=gap,
                   levels=levels, invalid_price=H - cfg("stage8.invalid_ratio", 0.886) * (H - L),
                   bounce_pct=bounce, bullish_candle=bullish, long_lower_wick=lower_wick,
                   volume_exhausted=exhausted, confirms=confirms, score=round(min(100.0, s), 1),
                   notes=notes)


def apply(c: Candidate, rt: Retrace, cfg) -> None:
    """분석 결과를 카드용 문장으로 붙인다(세력 말투)."""
    from settings import fmt_kst
    st = c.stage("retrace")
    st.score = rt.score
    lvl = LEVEL_NAME.get(rt.level, f"{rt.ratio:.2f}") if rt.level else f"{rt.ratio:.2f}(레벨 사이)"
    st.line = (f"고점 {_px(rt.high)}({fmt_kst(rt.high_ts, '%m/%d')}) 대비 −{rt.drawdown_pct:.0f}%, "
               f"직전 상승 +{rt.leg_gain_pct:.0f}%의 {lvl} 되돌림으로 보입니다")
    st.detail = {
        "fib": " · ".join(f"{f} {_px(p)}" for f, p in rt.levels.items()),
        "confirm": (f"저점 대비 +{rt.bounce_pct:.1f}% 반등"
                    + (" · 매도 거래량 감소" if rt.volume_exhausted else "")
                    + (" · 양봉" if rt.bullish_candle else "")
                    + (" · 아래꼬리" if rt.long_lower_wick else "")),
        "invalid": f"{_px(rt.invalid_price)} 이탈 시 상승 구조 무효",
        "confirms": rt.confirms,
    }
    c.tags += rt.notes
    c.score = rt.score


def _px(v: float) -> str:
    return f"${v:,.4g}" if v < 1000 else f"${v:,.0f}"


async def run(cands: list[Candidate], bitget, cfg, concurrency: int = 8) -> list[Candidate]:
    """비트겟 일봉으로 전 후보를 분석. 조건을 만족하고 반등 확인이 충분한 코인만 돌려준다."""
    sem = asyncio.Semaphore(concurrency)
    days = int(cfg("stage8.swing_window_days", 60)) + int(cfg("stage8.leg_lookback_days", 45))
    need = int(cfg("stage8.require_confirms", 2))
    out: list[Candidate] = []

    async def one(c: Candidate):
        sym, fut = (c.bitget_spot, False) if c.bitget_spot else (c.bitget_futures, True)
        if not sym:
            return
        async with sem:
            rows = await bitget.daily_candles(sym, min(days, 200), futures=fut)
        if not rows:
            return
        try:
            rt = analyze(rows, cfg)
        except Exception as e:  # noqa: BLE001
            log.warning("Stage8 %s 분석 실패: %s", c.symbol, e)
            return
        if rt and rt.confirms >= need:
            apply(c, rt, cfg)
            out.append(c)

    await asyncio.gather(*(one(c) for c in cands))
    return sorted(out, key=lambda c: -c.score)
