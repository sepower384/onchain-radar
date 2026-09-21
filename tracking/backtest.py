"""가중치 튜닝 백테스트 — 쌓인 신호(Stage 세부 점수)와 실제 수익률로 가중치를 다시 매긴다.

  python -m tracking.backtest              기본: 7일 BTC 대비 초과수익을 최대화
  python -m tracking.backtest --h 3 --metric ret

방법: 신호마다 저장해 둔 Stage 점수로 **가중치만 바꿔 다시 점수를 매기고**,
      상위 N 개 신호의 평균 성과가 가장 좋은 가중치 조합을 격자 탐색으로 찾는다.
      (재정규화 규칙은 실전과 똑같이 — 없는 신호는 빼고 나눈다.)

주의: 표본이 적을 때 나온 "최적"은 과적합이다. n 이 최소 50 이 안 되면 결과를 쓰지 말 것.
"""
from __future__ import annotations

import argparse
import itertools
import statistics

from sqlalchemy import select

import settings as S
from storage.db import Signal, SignalReturn, Store

SIGS = ("accumulation", "reaction", "derivatives", "revenue", "news", "security")


def rescore(stages: dict, w: dict) -> float:
    num = den = 0.0
    for k in SIGS:
        v = stages.get(k)
        if v is None or w.get(k, 0) <= 0:
            continue
        num += w[k] * v
        den += w[k]
    return num / den if den else 0.0


def load(store: Store, horizon: int, metric: str):
    with store.session() as s:
        sigs = {g.id: g for g in s.scalars(select(Signal).where(Signal.track == "main"))}
        rets = [r for r in s.scalars(select(SignalReturn).where(SignalReturn.horizon_days == horizon))]
    rows = []
    for r in rets:
        g = sigs.get(r.signal_id)
        if not g:
            continue
        y = r.excess_pct if metric == "excess" else r.ret_pct
        if y is None:
            continue
        rows.append((g.stages, y))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=int, default=7)
    ap.add_argument("--metric", choices=["excess", "ret"], default="excess")
    ap.add_argument("--top", type=float, default=0.3, help="상위 몇 %를 '샀다'고 볼지 (0.3 = 30%)")
    a = ap.parse_args()
    s = S.load()
    rows = load(Store(s.get("storage.url")), a.h, a.metric)
    print(f"표본 {len(rows)}건 ({a.h}일 · {a.metric})")
    if len(rows) < 20:
        print("표본이 너무 적습니다 — 최소 50건은 쌓인 뒤에 돌리세요.")
        return
    grid = [0.0, 0.1, 0.2, 0.3, 0.4]
    base = s.get("scoring.weights", {})
    results = []
    for combo in itertools.product(grid, repeat=4):          # 반응·파생·매출·매집 (호재·안전은 고정)
        w = {**base, "reaction": combo[0], "derivatives": combo[1], "revenue": combo[2], "accumulation": combo[3]}
        if sum(combo) == 0:
            continue
        ranked = sorted(rows, key=lambda r: -rescore(r[0], w))
        k = max(5, int(len(ranked) * a.top))
        results.append((statistics.mean(y for _, y in ranked[:k]), w))
    results.sort(key=lambda x: -x[0])
    cur = sorted(rows, key=lambda r: -rescore(r[0], base))
    k = max(5, int(len(cur) * a.top))
    print(f"현재 가중치 상위 {a.top:.0%} 평균: {statistics.mean(y for _, y in cur[:k]):+.2f}%")
    for score, w in results[:5]:
        print(f"  {score:+.2f}%  " + " ".join(f"{n}={w[n]}" for n in ("reaction", "derivatives", "revenue", "accumulation")))
    if len(rows) < 50:
        print("\n⚠ 표본 50건 미만 — 위 결과는 과적합일 가능성이 큽니다. 참고만 하세요.")


if __name__ == "__main__":
    main()
