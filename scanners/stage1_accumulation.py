"""Stage 1. 온체인 매집 — Phase 1 에서는 **스냅샷 적재**가 본업이다.

홀더 수·상위 10 집중도는 API 가 과거값을 주지 않는다. 그래서 봇이 주기적으로 GoPlus 값을
DB 에 쌓고, 변화율은 **스냅샷끼리** 비교해 계산한다(사양서).
데이터가 쌓이기 전에는 "데이터 수집 중" — 점수 None, **절대 추정값을 만들지 않는다.**

점수 (24시간 이상 쌓였을 때)
  홀더 24h 증가율 : 0% → 40점, +2% 이상 → 80점 (선형)
  상위10 집중도가 **떨어졌으면** 가점(+최대 20) — 고래 몇이 쥐고 있던 물량이 퍼지는 중
  올랐으면 감점(−최대 20)

Phase 2 에서 여기에 스마트머니·거래소 순유출이 더해진다(키·핫월렛 목록 필요).
"""
from __future__ import annotations

import logging
import time

from .model import Candidate

log = logging.getLogger("stage1")

MIN_HISTORY_H = 20        # 이보다 짧으면 "24h 변화"라 부르지 않는다


def evaluate(c: Candidate, history: list, now: float | None = None) -> None:
    st = c.stage("accumulation")
    now = time.time() if now is None else now
    pts = [h for h in history if h.holder_count]
    if len(pts) < 2 or (now - pts[0].ts) / 3600 < MIN_HISTORY_H:
        have = (now - pts[0].ts) / 3600 if pts else 0
        st.score, st.line = None, f"홀더 데이터 수집 중입니다 ({have:.0f}h / {MIN_HISTORY_H}h)"
        return
    # 24시간 전에 가장 가까운 점과 최신 점을 비교
    target = now - 86400
    old = min(pts, key=lambda h: abs(h.ts - target))
    new = pts[-1]
    growth = (new.holder_count / old.holder_count - 1) * 100 if old.holder_count else 0.0
    s = 40 + max(-40.0, min(40.0, growth / 2.0 * 40))
    conc_note = ""
    if old.top10_pct is not None and new.top10_pct is not None:
        d = new.top10_pct - old.top10_pct
        s += max(-20.0, min(20.0, -d * 4))
        conc_note = f", 상위10 집중도 {d:+.1f}%p"
    st.score = round(max(0.0, min(100.0, s)), 1)
    st.detail = {"holder_growth_24h_pct": round(growth, 2), "holders": new.holder_count}
    st.line = f"홀더 24h {growth:+.2f}% ({new.holder_count:,}명){conc_note}"


def snapshot(store, c: Candidate) -> None:
    """GoPlus 가 준 홀더 값을 적재. 보안 조회 직후에 부른다."""
    sec = c.security
    if sec is None or getattr(sec, "holder_count", None) is None:
        return
    store.add_holder_snapshot(c.coin_key, sec.holder_count, sec.top10_pct)


def run(cands: list[Candidate], store) -> None:
    week_ago = time.time() - 7 * 86400
    for c in cands:
        try:
            snapshot(store, c)
            evaluate(c, store.holder_history(c.coin_key, week_ago))
        except Exception as e:  # noqa: BLE001
            log.warning("Stage1 %s 실패: %s", c.symbol, e)
            c.stage("accumulation").score = None
