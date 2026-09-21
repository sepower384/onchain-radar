"""Stage 5. 매출 급증 감지 — "매출은 뛰는데 가격은 아직" 을 찾는다.

재료: DefiLlama 매출(부모 프로토콜 단위로 합산) → gecko_id 로 후보와 연결.
      매핑은 DefiLlama `/lite/protocols2` 의 부모 gecko_id 로 자동 — 손으로 표를 만들지 않는다.

점수
  비율 = 최근 7일 일평균 매출 / 직전 30일 일평균
  비율 < 1      → 20점 (매출이 줄고 있다)
  1 ~ 급증선   → 20 ~ 60점
  급증선 ~ 3배 → 60 ~ 100점
  7일 매출이 min_7d_revenue_usd 보다 작으면 비율이 튀어도 노이즈 — 점수는 주되 급증 판정은 안 한다
  급증 + 가격 7일 상승이 price_lag_max_7d_pct 미만 → "펀더멘털 선행" 태그, 보너스(=페널티 음수 대신 점수 가산)
매핑이 없는 코인 → None (매출 데이터 없음 = 재정규화에서 제외)
"""
from __future__ import annotations

import logging

from .model import Candidate

log = logging.getLogger("stage5")


def ratio_score(ratio: float | None, surge: float) -> float | None:
    if ratio is None:
        return None
    if ratio < 1:
        return 20.0
    if ratio < surge:
        return 20 + (ratio - 1) / (surge - 1) * 40
    return min(100.0, 60 + (ratio - surge) / max(3.0 - surge, 0.01) * 40)


def evaluate(c: Candidate, row: dict | None, cfg) -> None:
    st = c.stage("revenue")
    if not row:
        st.score, st.line = None, ""
        return
    surge = cfg("stage5.surge_ratio_min", 1.5)
    ratio = row.get("ratio")
    s = ratio_score(ratio, surge)
    if s is None:
        st.score, st.line = None, "매출 기준선 부족"
        return
    big_enough = ((row.get("rev_7d") or 0) >= cfg("stage5.min_7d_revenue_usd", 20000)
                  and (row.get("daily_base") or 0) >= cfg("stage5.min_base_daily_usd", 1000))
    st.detail = {k: row.get(k) for k in ("name", "rev_7d", "rev_30d", "ratio")}
    pct = (ratio - 1) * 100
    if ratio >= surge and big_enough:
        lag = cfg("stage5.price_lag_max_7d_pct", 10.0)
        if c.change_7d is not None and c.change_7d < lag:
            s = min(100.0, s + cfg("stage5.fundamental_lead_bonus", 15))
            c.tags.append("펀더멘털 선행")
            st.line = f"7d 매출 30d 평균 대비 {pct:+.0f}% — 가격은 아직 반응 전입니다(펀더멘털 선행)"
        else:
            st.line = f"7d 매출 30d 평균 대비 {pct:+.0f}%"
    elif not big_enough:
        st.line = f"매출 규모가 작아(7일 ${row.get('rev_7d', 0):,.0f}) 비율은 참고만 합니다"
    else:
        st.line = f"7d 매출 30d 평균 대비 {pct:+.0f}%"
    st.score = round(s, 1)


async def run(cands: list[Candidate], defillama, cfg) -> list[dict]:
    """후보에 매출 점수를 붙이고, /revenue 명령용 급증 랭킹을 돌려준다."""
    rows = await defillama.revenue_by_token()
    if rows is None:
        log.warning("DefiLlama 매출 조회 실패 — 매출 신호 비활성")
        for c in cands:
            c.stage("revenue").score = None
        return []
    by_gecko: dict[str, dict] = {}
    for r in rows:
        gid = r.get("gecko_id")
        if gid and (gid not in by_gecko or (r.get("rev_7d") or 0) > (by_gecko[gid].get("rev_7d") or 0)):
            by_gecko[gid] = r
    for c in cands:
        try:
            evaluate(c, by_gecko.get(c.cg_id or ""), cfg)
        except Exception as e:  # noqa: BLE001
            log.warning("Stage5 %s 실패: %s", c.symbol, e)
            c.stage("revenue").score = None

    return surge_ranking(rows, cfg)


def surge_ranking(rows: list[dict], cfg) -> list[dict]:
    """급증 랭킹. 기준선이 얕은 신규 프로토콜·토큰 없는 프로토콜은 뺀다."""
    surge = cfg("stage5.surge_ratio_min", 1.5)
    floor = cfg("stage5.min_7d_revenue_usd", 20000)
    base_min = cfg("stage5.min_base_daily_usd", 1000)
    need_token = cfg("stage5.require_token", True)
    out = [r for r in rows
           if (r.get("ratio") or 0) >= surge
           and (r.get("rev_7d") or 0) >= floor
           and (r.get("daily_base") or 0) >= base_min
           and (not need_token or r.get("gecko_id"))]
    return sorted(out, key=lambda r: -(r.get("ratio") or 0))
