"""종합 점수 = 사용 가능한 신호의 **재정규화** 가중합 − 페널티.

사양서 핵심: "어떤 신호의 데이터가 없는 코인은 그 신호를 0점 처리하지 말고
제외한 뒤 나머지 가중치를 재정규화한다."

예) 가중치 반응 0.25 · 파생 0.15 · 매출 0.15 · 안전 0.05, 매집·호재는 데이터 없음
    → 분모 = 0.60. 반응 80·파생 60·매출 None·안전 100 이면
      (0.25×80 + 0.15×60 + 0.05×100) / (0.25+0.15+0.05) = 34/0.45 = 75.6
"""
from __future__ import annotations

from scanners.model import SIGNALS, Candidate


def score(c: Candidate, weights: dict[str, float]) -> Candidate:
    num = den = 0.0
    used = 0
    parts = []
    for name in SIGNALS:
        st = c.stages.get(name)
        w = float(weights.get(name, 0) or 0)
        if st is None or st.score is None or w <= 0:
            continue
        v = max(0.0, min(100.0, st.score))
        num += w * v
        den += w
        used += 1
        parts.append((name, v, w))
    base = num / den if den > 0 else 0.0
    # 카드의 "점수 근거" — 재정규화한 가중치와 각 신호가 종합점수에 보탠 점수
    c.breakdown = [(n, v, w / den, v * w / den) for n, v, w in parts] if den else []
    penalty = sum(p for _, p in c.penalties)
    c.score = round(max(0.0, min(100.0, base - penalty)), 1)
    c.signals_used = used
    return c


def rank(cands: list[Candidate], weights: dict[str, float], min_signals: int = 2,
         required: tuple[str, ...] = ()) -> list[Candidate]:
    """점수를 매기고 정렬한다.
    - 사용된 신호가 너무 적은 코인은 뺀다(신호 하나짜리가 100점으로 맨 위에 오는 것 방지).
    - required 신호가 없는 코인도 뺀다. 파생(과열 없음)·안전(플래그 없음)은 "나쁜 게 없다"는
      신호일 뿐 "돈이 들어온다"는 신호가 아니다. 둘만으로 100점 받은 코인이 상위를 채웠다(첫 실행).
    """
    for c in cands:
        score(c, weights)
    ok = [c for c in cands if c.signals_used >= min_signals
          and all((c.stages.get(r) and c.stages[r].score is not None) for r in required)]
    return sorted(ok, key=lambda c: (-c.score, -(c.volume_24h or 0)))
